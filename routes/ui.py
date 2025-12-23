#!/usr/bin/python3
# coding=utf-8

"""Plugin UI Route"""

import os
import json
import flask

from pylon.core.tools import web


class Route:
    """UI route to serve static files"""

    # Route for ui_host proxy access: /ui/{toolkit_id}
    # project_id comes from X-Project-Id header
    @web.route("/ui/<int:toolkit_id>", endpoint="ui_route_proxy")
    @web.route("/ui/<int:toolkit_id>/assets/<path:asset_path>", endpoint="ui_route_proxy_assets")
    # Route for direct access: /ui/{project_id}/{toolkit_id}
    @web.route("/ui/<int:project_id>/<int:toolkit_id>", endpoint="ui_route_direct")
    @web.route("/ui/<int:project_id>/<int:toolkit_id>/assets/<path:asset_path>", endpoint="ui_route_direct_assets")
    def ui_route(self, project_id=None, toolkit_id=None, asset_path=None):
        """Serve static UI files"""
        # Get the plugin directory
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        static_dir = os.path.join(plugin_dir, "static", "ui", "dist")

        # If asset_path is provided, serve the asset file directly
        if asset_path:
            return flask.send_from_directory(static_dir, f"assets/{asset_path}")

        # Otherwise serve index.html with runtime config injection
        idx_path = os.path.join(static_dir, "index.html")

        if not os.path.exists(idx_path):
            return "UI not built. Please run: cd static/ui && ./build.sh", 404

        # Read index.html
        with open(idx_path, "r", encoding="utf-8") as idx_file:
            idx_data = idx_file.read()

        # Determine project_id and base_uri
        # When accessed via ui_host proxy, custom headers are injected:
        # X-Project-Id, X-User-Id, etc.
        # The full client path is: /app/ui_host/inventory/ui/{project_id}/{toolkit_id}
        # But the plugin only sees: /ui/{toolkit_id}
        header_project_id = flask.request.headers.get('X-Project-Id')

        # Use header project_id if available (ui_host proxy), otherwise use path param
        effective_project_id = header_project_id or project_id

        if header_project_id:
            # We're being accessed via ui_host proxy
            # Reconstruct the canonical base_uri: /app/ui_host/inventory/ui/{project_id}/{toolkit_id}
            base_uri = f"/app/ui_host/inventory/ui/{header_project_id}/{toolkit_id}"
        else:
            # Direct access to the plugin
            base_uri = f"/ui/{project_id}/{toolkit_id}" if project_id and toolkit_id else f"/ui/{toolkit_id}"

        # Create runtime config for the UI
        inventory_ui_config = {
            "base_uri": base_uri,
            "project_id": effective_project_id,
            "toolkit_id": toolkit_id,
        }

        # Inject config script
        config_script = f'<script>window.inventory_ui_config = {json.dumps(inventory_ui_config)};</script>'
        idx_data = idx_data.replace(
            '<!-- inventory_ui_config -->',
            config_script
        )

        # Rewrite asset paths to use base_uri
        # Vite builds with base: './' so assets are referenced as ./assets/...
        idx_data = idx_data.replace(
            'src="./assets', f'src="{base_uri}/assets'
        )
        idx_data = idx_data.replace(
            'href="./assets', f'href="{base_uri}/assets'
        )

        response = flask.make_response(idx_data, 200)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        return response
