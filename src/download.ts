import { createHash } from 'node:crypto';
import { createWriteStream } from 'node:fs';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import type { ShotwrightConfig } from './config.js';

export interface DownloadInstallerOptions {
    sourceUrl: string;
    product: string;
    platform: string;
    destinationFileName?: string;
    expectedSha256?: string;
    overwrite?: boolean;
}

export interface DownloadInstallerResult {
    product: string;
    platform: string;
    sourceUrl: string;
    destinationPath: string;
    fileName: string;
    bytes: number;
    contentType: string | null;
    sha256: string;
    overwritten: boolean;
}

function normalizeSlug(value: string): string {
    return value
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9._-]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'download';
}

function resolveFileName(sourceUrl: URL, destinationFileName?: string): string {
    const rawName = destinationFileName?.trim() || path.win32.basename(sourceUrl.pathname);
    const baseName = path.win32.basename(rawName);

    if (!baseName || baseName === '.' || baseName === '/') {
        throw new Error('destination file name could not be determined');
    }

    return baseName;
}

async function sha256File(filePath: string): Promise<string> {
    const hash = createHash('sha256');
    const handle = await fs.open(filePath, 'r');

    try {
        const input = handle.createReadStream();
        for await (const chunk of input) {
            hash.update(chunk);
        }
    } finally {
        await handle.close();
    }

    return hash.digest('hex');
}

function assertAllowedSource(config: ShotwrightConfig, sourceUrl: URL): void {
    if (sourceUrl.protocol !== 'https:') {
        throw new Error('only https download sources are allowed');
    }

    if (config.downloadAllowedHosts.length > 0) {
        const hostname = sourceUrl.hostname.toLowerCase();
        if (!config.downloadAllowedHosts.includes(hostname)) {
            throw new Error(`download host not allowed: ${hostname}`);
        }
    }
}

export async function downloadInstallerSource(
    config: ShotwrightConfig,
    options: DownloadInstallerOptions,
): Promise<DownloadInstallerResult> {
    const sourceUrl = new URL(options.sourceUrl);
    assertAllowedSource(config, sourceUrl);

    const product = normalizeSlug(options.product);
    const platform = normalizeSlug(options.platform);
    const fileName = resolveFileName(sourceUrl, options.destinationFileName);
    const targetDirectory = path.win32.join(config.downloadRoot, product, platform);
    const destinationPath = path.win32.join(targetDirectory, fileName);
    const temporaryPath = `${destinationPath}.partial`;
    const overwrite = options.overwrite ?? false;

    await fs.mkdir(targetDirectory, { recursive: true });

    if (!overwrite) {
        try {
            const stat = await fs.stat(destinationPath);
            const sha256 = await sha256File(destinationPath);

            if (options.expectedSha256 && sha256.toLowerCase() !== options.expectedSha256.toLowerCase()) {
                throw new Error(`existing file hash mismatch for ${destinationPath}`);
            }

            return {
                product,
                platform,
                sourceUrl: sourceUrl.toString(),
                destinationPath,
                fileName,
                bytes: stat.size,
                contentType: null,
                sha256,
                overwritten: false,
            };
        } catch (error) {
            const message = error instanceof Error ? error.message : '';
            if (message.startsWith('existing file hash mismatch')) {
                throw error;
            }
        }
    }

    await fs.rm(temporaryPath, { force: true });

    const response = await fetch(sourceUrl, {
        method: 'GET',
        redirect: 'follow',
    });

    if (!response.ok || !response.body) {
        throw new Error(`download failed with status ${response.status} ${response.statusText}`);
    }

    const hash = createHash('sha256');
    const output = createWriteStream(temporaryPath, { flags: 'w' });
    const readable = Readable.fromWeb(response.body as globalThis.ReadableStream<Uint8Array>);

    readable.on('data', (chunk: Buffer | string) => {
        hash.update(chunk);
    });

    try {
        await pipeline(readable, output);
        const sha256 = hash.digest('hex');

        if (options.expectedSha256 && sha256.toLowerCase() !== options.expectedSha256.toLowerCase()) {
            throw new Error(`downloaded file hash mismatch for ${fileName}`);
        }

        await fs.rm(destinationPath, { force: true });
        await fs.rename(temporaryPath, destinationPath);
        const stat = await fs.stat(destinationPath);

        return {
            product,
            platform,
            sourceUrl: sourceUrl.toString(),
            destinationPath,
            fileName,
            bytes: stat.size,
            contentType: response.headers.get('content-type'),
            sha256,
            overwritten: overwrite,
        };
    } catch (error) {
        await fs.rm(temporaryPath, { force: true });
        throw error;
    }
}