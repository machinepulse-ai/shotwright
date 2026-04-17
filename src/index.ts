import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { loadConfig } from './config.js';
import { cleanupValidationContainer, getRuntimeStatus, runValidationRender } from './validation.js';

const config = loadConfig();
const server = new McpServer({
    name: 'shotwright',
    version: '0.1.0',
});

server.tool(
    'shotwright_status',
    'Inspect Docker image availability, host Adobe After Effects paths, and local validation assets.',
    {},
    async () => {
        const status = await getRuntimeStatus(config);
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify(status, null, 2),
                },
            ],
        };
    },
);

server.tool(
    'shotwright_render_validation',
    'Run the validation After Effects render inside a Windows container mounted with the host Adobe installation.',
    {
        keepContainer: z.boolean().optional().default(false),
    },
    async ({ keepContainer }: { keepContainer: boolean }) => {
        const result = await runValidationRender(config, { keepContainer });
        return {
            content: [
                {
                    type: 'text',
                    text: JSON.stringify(result, null, 2),
                },
            ],
        };
    },
);

server.tool(
    'shotwright_cleanup_validation',
    'Remove the temporary validation container if it is still running.',
    {},
    async () => {
        await cleanupValidationContainer(config);
        return {
            content: [
                {
                    type: 'text',
                    text: 'shotwright validation container removed.',
                },
            ],
        };
    },
);

const transport = new StdioServerTransport();
await server.connect(transport);
