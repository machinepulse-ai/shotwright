import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { loadConfig } from './config.js';
import { downloadInstallerSource } from './download.js';
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

server.tool(
    'shotwright_download_installer_source',
    'Download a user-supplied official installer source into the local Shotwright cache. This tool does not discover Adobe endpoints; the caller must provide the source URL explicitly.',
    {
        sourceUrl: z.string().url(),
        product: z.string().optional().default('after-effects'),
        platform: z.string().optional().default('windows-x64'),
        destinationFileName: z.string().optional(),
        expectedSha256: z.string().regex(/^[a-fA-F0-9]{64}$/).optional(),
        overwrite: z.boolean().optional().default(false),
    },
    async ({
        sourceUrl,
        product,
        platform,
        destinationFileName,
        expectedSha256,
        overwrite,
    }: {
        sourceUrl: string;
        product: string;
        platform: string;
        destinationFileName?: string;
        expectedSha256?: string;
        overwrite: boolean;
    }) => {
        const result = await downloadInstallerSource(config, {
            sourceUrl,
            product,
            platform,
            destinationFileName,
            expectedSha256,
            overwrite,
        });

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

const transport = new StdioServerTransport();
await server.connect(transport);
