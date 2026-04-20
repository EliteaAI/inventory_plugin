#!/usr/bin/python3
# coding=utf-8

"""Platform Registration Event"""

import time

import requests

from pylon.core.tools import log, web


class Event:
    """
    Event Resource

    self is pointing to current Module instance

    Note: web.event decorator must be the last decorator (at top)
    """

    @web.event("pylon_modules_initialized")
    def handle_pylon_modules_initialized(self, _context, _event, payload):
        """Register provider with the AI/Run platform on startup"""
        event_pylon_id = payload
        if self.context.id != event_pylon_id:
            return

        # Get registration configuration
        ai_run_platform_url = self.descriptor.config.get("ai_run_platform_url", None)
        ai_run_platform_token = self.descriptor.config.get("ai_run_platform_token", None)
        ai_run_platform_verify = self.descriptor.config.get("ai_run_platform_verify", False)
        ai_run_platform_timeout = self.descriptor.config.get("ai_run_platform_timeout", 120)
        ai_run_platform_delay = self.descriptor.config.get("ai_run_platform_delay", 5)

        if ai_run_platform_url is not None and ai_run_platform_url:
            log.info("Will register Inventory provider in %s seconds", ai_run_platform_delay)

            time.sleep(ai_run_platform_delay)

            log.info("Registering Inventory provider descriptor")

            # Get provider descriptor
            descriptor = self.provider_descriptor()

            # Prepare authorization header if token provided
            headers = None
            if ai_run_platform_token is not None and ai_run_platform_token:
                headers = {
                    "Authorization": f"Bearer {ai_run_platform_token}",
                }

            try:
                register_result = requests.post(
                    ai_run_platform_url,
                    headers=headers,
                    json=descriptor,
                    verify=ai_run_platform_verify,
                    timeout=ai_run_platform_timeout,
                )

                register_result.raise_for_status()

                log.info("Inventory provider registration successful: %s", register_result.status_code)
            except Exception:
                log.exception("Failed to register Inventory provider with platform")
