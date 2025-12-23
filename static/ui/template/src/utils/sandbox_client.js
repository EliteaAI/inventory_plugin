/**
 * Lightweight Alita Client for JavaScript
 * Migrated from Python SandboxClient
 */

class ApiDetailsRequestError extends Error {
    constructor(message) {
        super(message);
        this.name = 'ApiDetailsRequestError';
    }
}

class SandboxArtifact {
    constructor(client, bucketName) {
        this.client = client;
        this.bucketName = bucketName;
    }

    async init() {
        const exists = await this.client.bucketExists(this.bucketName);
        if (!exists) {
            await this.client.createBucket(this.bucketName);
        }
    }

    async create(artifactName, artifactData, bucketName = null) {
        try {
            const bucket = bucketName || this.bucketName;
            const result = await this.client.createArtifact(bucket, artifactName, artifactData);
            return JSON.stringify(result);
        } catch (error) {
            console.error(`Error: ${error}`);
            return `Error: ${error.message}`;
        }
    }

    async get(artifactName, bucketName = null) {
        const bucket = bucketName || this.bucketName;
        const data = await this.client.downloadArtifact(bucket, artifactName);
        
        if (data.length === 0) {
            return '';
        }
        
        if (data.error) {
            return `${data.error}. ${data.content || ''}`;
        }
        
        return data;
    }

    async delete(artifactName, bucketName = null) {
        const bucket = bucketName || this.bucketName;
        await this.client.deleteArtifact(bucket, artifactName);
    }

    async list(bucketName = null, returnAsString = true) {
        const bucket = bucketName || this.bucketName;
        const artifacts = await this.client.listArtifacts(bucket);
        return returnAsString ? JSON.stringify(artifacts) : artifacts;
    }

    async append(artifactName, additionalData, bucketName = null) {
        const bucket = bucketName || this.bucketName;
        let data = await this.get(artifactName, bucket);
        
        if (data === 'Could not detect encoding') {
            return data;
        }
        
        data += data.length > 0 ? `${additionalData}` : additionalData;
        await this.client.createArtifact(bucket, artifactName, data);
        return 'Data appended successfully';
    }

    async overwrite(artifactName, newData, bucketName = null) {
        const bucket = bucketName || this.bucketName;
        return await this.create(artifactName, newData, bucket);
    }

    async getContentBytes(artifactName, bucketName = null) {
        const bucket = bucketName || this.bucketName;
        return await this.client.downloadArtifact(bucket, artifactName);
    }
}

class SandboxClient {
    constructor(config) {
        const {
            baseUrl,
            projectId,
            authToken,
            apiExtraHeaders = {},
            configurations = [],
            XSECRET = 'secret',
            modelTimeout = 120,
            modelImageGeneration = null
        } = config;

        this.baseUrl = baseUrl.replace(/\/$/, '');
        this.apiPath = '/api/v2';
        this.llmPath = '/llm/v1';
        this.projectId = projectId;
        this.authToken = authToken;
        this.headers = {
            'Authorization': `Bearer ${authToken}`,
            'X-SECRET': XSECRET,
            'Content-Type': 'application/json',
            ...apiExtraHeaders
        };
        
        // API endpoints
        this.predictUrl = `${this.baseUrl}${this.apiPath}/prompt_lib/predict/prompt_lib/${this.projectId}`;
        this.promptVersions = `${this.baseUrl}${this.apiPath}/prompt_lib/version/prompt_lib/${this.projectId}`;
        this.prompts = `${this.baseUrl}${this.apiPath}/prompt_lib/prompt/prompt_lib/${this.projectId}`;
        this.datasources = `${this.baseUrl}${this.apiPath}/datasources/datasource/prompt_lib/${this.projectId}`;
        this.datasourcesPredict = `${this.baseUrl}${this.apiPath}/datasources/predict/prompt_lib/${this.projectId}`;
        this.datasourcesSearch = `${this.baseUrl}${this.apiPath}/datasources/search/prompt_lib/${this.projectId}`;
        this.app = `${this.baseUrl}${this.apiPath}/applications/application/prompt_lib/${this.projectId}`;
        this.mcpToolsList = `${this.baseUrl}${this.apiPath}/mcp_sse/tools_list/${this.projectId}`;
        this.mcpToolsCall = `${this.baseUrl}${this.apiPath}/mcp_sse/tools_call/${this.projectId}`;
        this.applicationVersions = `${this.baseUrl}${this.apiPath}/applications/version/prompt_lib/${this.projectId}`;
        this.listAppsUrl = `${this.baseUrl}${this.apiPath}/applications/applications/prompt_lib/${this.projectId}`;
        this.integrationDetails = `${this.baseUrl}${this.apiPath}/integrations/integration/${this.projectId}`;
        this.secretsUrl = `${this.baseUrl}${this.apiPath}/secrets/secret/${this.projectId}`;
        this.artifactsUrl = `${this.baseUrl}${this.apiPath}/artifacts/artifacts/default/${this.projectId}`;
        this.artifactUrl = `${this.baseUrl}${this.apiPath}/artifacts/artifact/default/${this.projectId}`;
        this.bucketUrl = `${this.baseUrl}${this.apiPath}/artifacts/buckets/${this.projectId}`;
        this.configurationsUrl = `${this.baseUrl}${this.apiPath}/integrations/integrations/default/${this.projectId}?section=configurations&unsecret=true`;
        this.aiSectionUrl = `${this.baseUrl}${this.apiPath}/integrations/integrations/default/${this.projectId}?section=ai`;
        this.imageGenerationUrl = `${this.baseUrl}${this.llmPath}/images/generations`;
        this.authUserUrl = `${this.baseUrl}${this.apiPath}/auth/user`;
        this.testToolUrl = `${this.baseUrl}${this.apiPath}/applications/test_tool/${this.projectId}`;
        this.taskUrl = `${this.baseUrl}${this.apiPath}/chat/task/prompt_lib/${this.projectId}`;
        this.applicationTaskUrl = `${this.baseUrl}${this.apiPath}/elitea_core/application_task/prompt_lib/${this.projectId}`;
        
        this.configurations = configurations;
        this.modelTimeout = modelTimeout;
        this.modelImageGeneration = modelImageGeneration;
        this._userIdCache = null;
        this.withCredentials = config.withCredentials !== false; // Default to true for session cookie sharing
    }

    /**
     * Update auth token (useful for iframe integration where session is shared from parent)
     * @param {string} token - New auth token
     */
    updateAuthToken(token) {
        this.authToken = token;
        this.headers['Authorization'] = `Bearer ${token}`;
    }

    /**
     * Update configuration (useful for dynamic reconfiguration)
     * @param {Object} config - Configuration updates
     */
    updateConfig(config) {
        if (config.baseUrl !== undefined) {
            this.baseUrl = config.baseUrl.replace(/\/$/, '');
        }
        if (config.projectId !== undefined) {
            this.projectId = config.projectId;
        }
        if (config.authToken !== undefined) {
            this.updateAuthToken(config.authToken);
        }
        if (config.apiExtraHeaders !== undefined) {
            this.headers = { ...this.headers, ...config.apiExtraHeaders };
        }
        if (config.XSECRET !== undefined) {
            this.headers['X-SECRET'] = config.XSECRET;
        }
        if (config.withCredentials !== undefined) {
            this.withCredentials = config.withCredentials;
        }
    }

    async _fetch(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                ...this.headers,
                ...options.headers
            },
            credentials: this.withCredentials ? 'include' : 'same-origin' // Enable session cookie sharing
        });
        
        return response;
    }

    async _getRealUserId() {
        if (this._userIdCache) {
            return this._userIdCache;
        }
        
        try {
            const userData = await this.getUserData();
            this._userIdCache = userData.id;
            return this._userIdCache;
        } catch (error) {
            console.error('Failed to get user ID:', error);
            return null;
        }
    }

    async getMcpToolkits() {
        const userId = await this._getRealUserId();
        if (!userId) {
            return [];
        }
        
        const url = `${this.mcpToolsList}/${userId}`;
        const response = await this._fetch(url);
        return await response.json();
    }

    async mcpToolCall(params) {
        const userId = await this._getRealUserId();
        if (!userId) {
            return 'Error: Could not determine user ID for MCP tool call';
        }
        
        const url = `${this.mcpToolsCall}/${userId}`;
        
        // Handle Pydantic-like objects (objects with toJSON or dict methods)
        const processedParams = JSON.parse(JSON.stringify(params));
        
        const response = await this._fetch(url, {
            method: 'POST',
            body: JSON.stringify(processedParams)
        });
        
        try {
            return await response.json();
        } catch (error) {
            return await response.text();
        }
    }

    async getAppDetails(applicationId) {
        const url = `${this.app}/${applicationId}`;
        const response = await this._fetch(url);
        return await response.json();
    }

    async getListOfApps() {
        const apps = [];
        const limit = 10;
        let offset = 0;
        let totalCount = null;

        while (totalCount === null || offset < totalCount) {
            const params = new URLSearchParams({ offset, limit });
            const response = await this._fetch(`${this.listAppsUrl}?${params}`);
            
            if (response.ok) {
                const data = await response.json();
                totalCount = data.total;
                apps.push(...data.rows.map(app => ({ name: app.name, id: app.id })));
                offset += limit;
            } else {
                break;
            }
        }

        return apps;
    }

    async fetchAvailableConfigurations() {
        const response = await this._fetch(this.configurationsUrl);
        if (response.ok) {
            return await response.json();
        }
        return [];
    }

    async allModelsAndIntegrations() {
        const response = await this._fetch(this.aiSectionUrl);
        if (response.ok) {
            return await response.json();
        }
        return [];
    }

    async generateImage({
        prompt,
        n = 1,
        size = 'auto',
        quality = 'auto',
        responseFormat = 'b64_json',
        style = null
    }) {
        if (!this.modelImageGeneration) {
            throw new Error('Image generation model is not configured for this client');
        }

        const imageGenerationData = {
            prompt,
            model: this.modelImageGeneration,
            n,
            response_format: responseFormat
        };

        if (size && size.toLowerCase() !== 'auto') {
            imageGenerationData.size = size;
        }

        if (quality && quality.toLowerCase() !== 'auto') {
            imageGenerationData.quality = quality;
        }

        if (style) {
            imageGenerationData.style = style;
        }

        console.log(`Generating image with model: ${this.modelImageGeneration}, prompt: ${prompt.substring(0, 50)}...`);

        const response = await this._fetch(this.imageGenerationUrl, {
            method: 'POST',
            body: JSON.stringify(imageGenerationData)
        });

        if (!response.ok) {
            const errorText = await response.text();
            console.error(`Image generation failed: ${response.status} - ${errorText}`);
            throw new Error(`Image generation failed: ${response.status}`);
        }

        return await response.json();
    }

    async getAppVersionDetails(applicationId, applicationVersionId) {
        const url = `${this.applicationVersions}/${applicationId}/${applicationVersionId}`;
        const configs = this.configurations.length > 0 
            ? this.configurations 
            : await this.fetchAvailableConfigurations();

        const response = await this._fetch(url, {
            method: 'PATCH',
            body: JSON.stringify({ configurations: configs })
        });

        if (response.ok) {
            return await response.json();
        }

        console.error(`Failed to fetch application version details: ${response.status} - ${await response.text()}`);
        throw new ApiDetailsRequestError(
            `Failed to fetch application version details for ${applicationId}/${applicationVersionId}.`
        );
    }

    async getIntegrationDetails(integrationId, formatForModel = false) {
        const url = `${this.integrationDetails}/${integrationId}`;
        const response = await this._fetch(url);
        return await response.json();
    }

    async unsecret(secretName) {
        const url = `${this.secretsUrl}/${secretName}`;
        const response = await this._fetch(url);
        const data = await response.json();
        console.log('Unsecret response:', data);
        return data.value || null;
    }

    artifact(bucketName) {
        const artifact = new SandboxArtifact(this, bucketName);
        return artifact.init().then(() => artifact);
    }

    _processRequest(response, data) {
        if (response.status === 403) {
            return { error: 'You are not authorized to access this resource' };
        } else if (response.status === 404) {
            return { error: 'Resource not found' };
        } else if (response.status !== 200) {
            return {
                error: 'An error occurred while fetching the resource',
                content: data
            };
        }
        return data;
    }

    async bucketExists(bucketName) {
        try {
            const response = await this._fetch(this.bucketUrl);
            const data = await response.json();
            const result = this._processRequest(response, data);
            
            if (result.error) {
                return false;
            }
            
            for (const bucket of result.rows || []) {
                if (bucket.name === bucketName) {
                    return true;
                }
            }
            return false;
        } catch (error) {
            return false;
        }
    }

    async createBucket(bucketName, expirationMeasure = 'months', expirationValue = 1) {
        const postData = {
            name: bucketName,
            expiration_measure: expirationMeasure,
            expiration_value: expirationValue
        };
        
        const response = await this._fetch(this.bucketUrl, {
            method: 'POST',
            body: JSON.stringify(postData)
        });
        
        const data = await response.json();
        return this._processRequest(response, data);
    }

    async listArtifacts(bucketName) {
        const url = `${this.artifactsUrl}/${bucketName.toLowerCase()}`;
        const response = await this._fetch(url);
        const data = await response.json();
        return this._processRequest(response, data);
    }

    async createArtifact(bucketName, artifactName, artifactData) {
        const url = `${this.artifactsUrl}/${bucketName.toLowerCase()}`;
        const formData = new FormData();
        
        // Handle different data types
        let blob;
        if (artifactData instanceof Blob) {
            blob = artifactData;
        } else if (typeof artifactData === 'string') {
            blob = new Blob([artifactData], { type: 'text/plain' });
        } else {
            blob = new Blob([JSON.stringify(artifactData)], { type: 'application/json' });
        }
        
        formData.append('file', blob, artifactName);
        
        const response = await this._fetch(url, {
            method: 'POST',
            body: formData,
            headers: {
                'Authorization': this.headers.Authorization,
                'X-SECRET': this.headers['X-SECRET']
                // Don't set Content-Type for FormData, let browser set it with boundary
            }
        });
        
        const data = await response.json();
        return this._processRequest(response, data);
    }

    async downloadArtifact(bucketName, artifactName) {
        const url = `${this.artifactUrl}/${bucketName.toLowerCase()}/${artifactName}`;
        const response = await this._fetch(url);
        
        if (response.status === 403) {
            return { error: 'You are not authorized to access this resource' };
        } else if (response.status === 404) {
            return { error: 'Resource not found' };
        } else if (response.status !== 200) {
            return {
                error: 'An error occurred while fetching the resource',
                content: await response.text()
            };
        }
        
        return await response.arrayBuffer();
    }

    async deleteArtifact(bucketName, artifactName) {
        const url = `${this.artifactUrl}/${bucketName}?filename=${encodeURIComponent(artifactName)}`;
        const response = await this._fetch(url, { method: 'DELETE' });
        const data = await response.json();
        return this._processRequest(response, data);
    }

    async getUserData() {
        const response = await this._fetch(this.authUserUrl);
        if (response.ok) {
            return await response.json();
        }
        console.error(`Failed to fetch user data: ${response.status} - ${await response.text()}`);
        throw new ApiDetailsRequestError(`Failed to fetch user data with status code ${response.status}.`);
    }

    /**
     * Test a tool synchronously
     * @param {number} toolId - The ID of the tool to test
     * @param {Object} params - Test parameters
     * @param {string} params.tool - Tool name
     * @param {string} params.testing_name - Name for the test
     * @param {Object} params.input - Input parameters for the tool
     * @param {Object} params.output - Expected output configuration
     * @param {Object} params.input_mapping - Input mapping configuration
     * @param {Object} params.user_input - User input for the test
     * @param {string} [params.call_type='tool'] - Type of call
     * @param {number} [timeout=300] - Timeout in seconds (default: 300 seconds / 5 minutes)
     * @returns {Promise<Object>} - Test result
     */
    async testToolSync(toolId, params, timeout = 300) {
        const url = `${this.testToolUrl}/${toolId}?await_response=true&call_type=${params.call_type || 'tool'}&timeout=${timeout}`;
        
        const payload = {
            tool: params.tool,
            testing_name: params.testing_name || 'test_execution',
            input: params.input || {},
            output: params.output || {},
            structured_output: params.structured_output || null,
            input_mapping: params.input_mapping || {},
            transition: params.transition || {},
            input_variables: params.input_variables || [],
            user_input: params.user_input || '',
            sid: null // No SID for sync calls
        };

        const response = await this._fetch(url, {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(`Tool test failed: ${JSON.stringify(error)}`);
        }

        return await response.json();
    }

    /**
     * Test a tool asynchronously
     * @param {number} toolId - The ID of the tool to test
     * @param {Object} params - Test parameters (same as testToolSync)
     * @param {string} [sid=null] - Optional Socket.IO session ID for streaming results. If not provided, results must be retrieved via polling.
     * @param {number} [timeout=-1] - Timeout in seconds (default: -1 for no timeout in async mode)
     * @returns {Promise<Object>} - Object containing task_id for tracking
     */
    async testToolAsync(toolId, params, sid = null, timeout = -1) {
        const url = `${this.testToolUrl}/${toolId}?await_response=false&call_type=${params.call_type || 'tool'}&timeout=${timeout}`;
        
        const payload = {
            tool: params.tool,
            testing_name: params.testing_name || 'test_execution',
            input: params.input || {},
            output: params.output || {},
            structured_output: params.structured_output || null,
            input_mapping: params.input_mapping || {},
            transition: params.transition || {},
            input_variables: params.input_variables || [],
            user_input: params.user_input || '',
            sid: sid
        };

        const response = await this._fetch(url, {
            method: 'POST',
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(`Tool test failed: ${JSON.stringify(error)}`);
        }

        const result = await response.json();
        return result; // Contains task_id
    }

    /**
     * Stop a running task
     * @param {string} messageGroupUuid - The UUID of the message group (task)
     * @returns {Promise<void>}
     */
    async stopTask(messageGroupUuid) {
        const url = `${this.taskUrl}/${messageGroupUuid}`;
        const response = await this._fetch(url, { method: 'DELETE' });
        
        if (!response.ok && response.status !== 204) {
            const error = await response.text();
            throw new Error(`Failed to stop task: ${error}`);
        }
    }

    /**
     * Get task status
     * @param {string} taskId - Task ID to check
     * @param {boolean} withMeta - Include task metadata
     * @param {boolean} withResult - Include task result
     * @returns {Promise<Object>} - Task status information
     */
    async getTaskStatus(taskId, withMeta = false, withResult = false) {
        const params = new URLSearchParams();
        if (withMeta) params.append('meta', 'yes');
        if (withResult) params.append('result', 'yes');
        
        const url = `${this.applicationTaskUrl}/${taskId}${params.toString() ? '?' + params.toString() : ''}`;
        const response = await this._fetch(url);
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(`Failed to get task status: ${JSON.stringify(error)}`);
        }
        
        return await response.json();
    }

    /**
     * Stop a task by task ID
     * @param {string} taskId - Task ID to stop
     * @returns {Promise<void>}
     */
    async stopTaskById(taskId) {
        const url = `${this.applicationTaskUrl}/${taskId}`;
        const response = await this._fetch(url, { method: 'DELETE' });
        
        if (!response.ok && response.status !== 204) {
            const error = await response.text();
            throw new Error(`Failed to stop task: ${error}`);
        }
    }

    /**
     * Poll for task completion with actual status checks
     * @param {string} taskId - Task ID to monitor
     * @param {Function} callback - Callback function for status updates (optional)
     * @param {number} maxAttempts - Maximum polling attempts
     * @param {number} interval - Polling interval in ms
     * @returns {Promise<Object>} - Final task result
     */
    async pollTaskStatus(taskId, callback = null, maxAttempts = 60, interval = 1000) {
        let attempts = 0;
        
        return new Promise((resolve, reject) => {
            const poll = setInterval(async () => {
                attempts++;
                
                try {
                    const status = await this.getTaskStatus(taskId, true, false);
                    
                    if (callback) {
                        callback({
                            task_id: taskId,
                            status: status.status,
                            meta: status.meta,
                            attempts: attempts
                        });
                    }
                    
                    // Check if task is complete (not running or pending)
                    if (status.status && !['PENDING', 'STARTED', 'RETRY'].includes(status.status)) {
                        clearInterval(poll);
                        
                        // Get final result
                        const finalResult = await this.getTaskStatus(taskId, true, true);
                        resolve(finalResult);
                    }
                    
                    if (attempts >= maxAttempts) {
                        clearInterval(poll);
                        reject(new Error('Task polling timeout'));
                    }
                } catch (error) {
                    clearInterval(poll);
                    reject(error);
                }
            }, interval);
        });
    }

    /**
     * Wait for async task to complete
     * @param {string} taskId - Task ID from async operation
     * @param {number} timeout - Timeout in seconds
     * @returns {Promise<Object>} - Task result
     */
    async waitForTask(taskId, timeout = 300) {
        const maxAttempts = Math.floor(timeout);
        const result = await this.pollTaskStatus(taskId, null, maxAttempts, 1000);
        
        if (result.status === 'SUCCESS') {
            return result.result;
        } else if (result.status === 'FAILURE') {
            throw new Error(`Task failed: ${JSON.stringify(result.result)}`);
        } else {
            throw new Error(`Task ended with unexpected status: ${result.status}`);
        }
    }

    /**
     * Get toolkit details including settings
     * @param {string|number} toolkitId - The toolkit ID
     * @returns {Promise<Object>} - Toolkit details
     */
    async getToolkitDetails(toolkitId) {
        const url = `${this.baseUrl}/api/v2/elitea_core/tool/prompt_lib/${this.projectId}/${toolkitId}`;
        const response = await this._fetch(url);
        
        if (!response.ok) {
            const error = await response.text();
            throw new Error(`Failed to get toolkit details: ${response.status} - ${error}`);
        }
        
        return await response.json();
    }

    /**
     * Update toolkit settings
     * @param {string|number} toolkitId - The toolkit ID
     * @param {Object} settings - The settings object to update
     * @returns {Promise<Object>} - Updated toolkit
     */
    async updateToolkitSettings(toolkitId, settings) {
        // First fetch the current toolkit data
        const toolkit = await this.getToolkitDetails(toolkitId);

        // Merge new settings with existing toolkit
        const updatedToolkit = {
            ...toolkit,
            settings: {
                ...toolkit.settings,
                ...settings,
            },
        };

        const url = `${this.baseUrl}/api/v2/elitea_core/tool/prompt_lib/${this.projectId}/${toolkitId}`;
        const response = await this._fetch(url, {
            method: 'PUT',
            body: JSON.stringify(updatedToolkit)
        });

        if (!response.ok) {
            const error = await response.text();
            throw new Error(`Failed to update toolkit settings: ${response.status} - ${error}`);
        }

        return await response.json();
    }

    /**
     * List toolkits with optional filters
     * @param {Object} options - Query options
     * @param {string[]} options.toolkitTypes - Filter by toolkit types (e.g., ['github', 'ado'])
     * @param {number} options.limit - Maximum number of results (default: 20)
     * @param {number} options.offset - Offset for pagination (default: 0)
     * @param {string} options.query - Search query string
     * @returns {Promise<{rows: Array, total: number}>} - List of toolkits
     */
    async listToolkits(options = {}) {
        const { toolkitTypes = [], limit = 20, offset = 0, query = '' } = options;

        const params = new URLSearchParams();
        params.append('limit', limit);
        params.append('offset', offset);

        if (query) {
            params.append('query', query);
        }

        // Handle multiple toolkit_type values
        toolkitTypes.forEach(type => {
            params.append('toolkit_type', type);
        });

        const url = `${this.baseUrl}/api/v2/elitea_core/tools/prompt_lib/${this.projectId}?${params.toString()}`;
        const response = await this._fetch(url);

        if (!response.ok) {
            const error = await response.text();
            throw new Error(`Failed to list toolkits: ${response.status} - ${error}`);
        }

        return await response.json();
    }
}

// Export for both CommonJS and ES modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { SandboxClient, SandboxArtifact, ApiDetailsRequestError };
}

// ES Module export
export { SandboxClient, SandboxArtifact, ApiDetailsRequestError };
export default SandboxClient;
