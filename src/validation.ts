import { promises as fs } from 'node:fs';
import path from 'node:path';
import type { Dirent } from 'node:fs';
import type { ShotwrightConfig } from './config.js';
import { validationPaths } from './config.js';
import { runCommand } from './shell.js';

export interface ValidationOptions {
    keepContainer?: boolean;
}

async function exists(targetPath: string): Promise<boolean> {
    try {
        await fs.access(targetPath);
        return true;
    } catch {
        return false;
    }
}

async function removeIfExists(targetPath: string): Promise<void> {
    if (await exists(targetPath)) {
        await fs.rm(targetPath, { force: true, recursive: true });
    }
}

function dockerVolume(hostPath: string, containerPath: string): string {
    return `${hostPath}:${containerPath}`;
}

async function dockerExecPowershell(config: ShotwrightConfig, script: string, allowNonZero = false) {
    return await runCommand(
        config.dockerBinary,
        ['exec', config.validationContainerName, 'powershell', '-NoProfile', '-Command', script],
        { allowNonZero },
    );
}

export async function cleanupValidationContainer(config: ShotwrightConfig): Promise<void> {
    await runCommand(config.dockerBinary, ['rm', '-f', config.validationContainerName], { allowNonZero: true });
}

export async function getRuntimeStatus(config: ShotwrightConfig) {
    const paths = validationPaths(config);
    const imageInspect = await runCommand(
        config.dockerBinary,
        ['image', 'inspect', config.imageTag, '--format', '{{.Id}}'],
        { allowNonZero: true },
    );

    return {
        imageTag: config.imageTag,
        imageExists: imageInspect.code === 0,
        imageId: imageInspect.stdout.trim() || null,
        aeBinaryExists: await exists(config.aeBinary),
        aeRenderBinaryExists: await exists(config.aeRenderBinary),
        createValidationScriptExists: await exists(paths.createProjectScript),
        validationJobExists: await exists(paths.validationJob),
        outputExists: await exists(paths.outputMp4),
    };
}

async function startValidationContainer(config: ShotwrightConfig): Promise<void> {
    await cleanupValidationContainer(config);
    await runCommand(config.dockerBinary, [
        'run',
        '-d',
        '--name',
        config.validationContainerName,
        '--isolation',
        'process',
        '-v',
        dockerVolume(config.aeRoot, config.aeRoot),
        '-v',
        dockerVolume(config.projectRoot, config.workspaceMount),
        '-v',
        dockerVolume(config.dataRoot, config.dataMount),
        '-w',
        config.workspaceMount,
        config.imageTag,
        'powershell',
        '-NoProfile',
        '-Command',
        'Start-Sleep -Seconds 36000',
    ]);
}

async function generateValidationAep(config: ShotwrightConfig): Promise<void> {
    const script = `& { Remove-Item '${config.dataMount}\\templates\\validation_motion.aep' -ErrorAction SilentlyContinue; $proc = Start-Process -FilePath '${config.aeBinary}' -ArgumentList '-r','${config.workspaceMount}\\scripts\\create_validation_animation_project.jsx' -PassThru; $proc | Wait-Process -Timeout 300; if (-not (Test-Path '${config.dataMount}\\templates\\validation_motion.aep')) { throw 'validation AEP not generated'; } }`;
    await dockerExecPowershell(config, script);
}

export async function runValidationRender(config: ShotwrightConfig, options: ValidationOptions = {}) {
    const paths = validationPaths(config);

    await fs.mkdir(path.win32.dirname(paths.outputMp4), { recursive: true });
    await fs.mkdir(path.win32.dirname(paths.templateAep), { recursive: true });
    await fs.mkdir(paths.workRoot, { recursive: true });

    await removeIfExists(paths.outputMp4);
    const workEntries = await fs.readdir(paths.workRoot, { withFileTypes: true });
    await Promise.all(
        workEntries
            .filter((entry) => entry.isDirectory())
            .map((entry) => fs.rm(path.win32.join(paths.workRoot, entry.name), { recursive: true, force: true })),
    );

    await startValidationContainer(config);

    try {
        await generateValidationAep(config);
        const render = await dockerExecPowershell(
            config,
            `& { & nexrender-cli.cmd -f '${config.workspaceMount}\\scripts\\validation_nexrender_job.json' -w '${config.dataMount}\\work' -b '${config.aeRenderBinary}' --skip-cleanup --debug; exit $LASTEXITCODE }`,
            true,
        );

        if (!(await exists(paths.outputMp4))) {
            throw new Error(`validation render did not produce ${paths.outputMp4}\n${render.stdout}\n${render.stderr}`);
        }

        const outputStat = await fs.stat(paths.outputMp4);
        const workDirs = (await fs.readdir(paths.workRoot, { withFileTypes: true }))
            .filter((entry: Dirent) => entry.isDirectory())
            .map((entry: Dirent) => entry.name);

        return {
            success: render.code === 0,
            exitCode: render.code,
            outputFile: paths.outputMp4,
            outputBytes: outputStat.size,
            workDirectories: workDirs,
            stdout: render.stdout,
            stderr: render.stderr,
        };
    } finally {
        if (!options.keepContainer) {
            await cleanupValidationContainer(config);
        }
    }
}
