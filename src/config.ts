import path from 'node:path';

export interface ShotwrightConfig {
    projectRoot: string;
    dockerBinary: string;
    imageTag: string;
    validationContainerName: string;
    downloadRoot: string;
    downloadAllowedHosts: string[];
    aeRoot: string;
    aeBinary: string;
    aeRenderBinary: string;
    dataRoot: string;
    workspaceMount: string;
    dataMount: string;
}

function envValue(...names: string[]): string | undefined {
    for (const name of names) {
        if (process.env[name]) {
            return process.env[name];
        }
    }

    return undefined;
}

export function loadConfig(): ShotwrightConfig {
    const projectRoot = envValue('SHOTWRIGHT_ROOT', 'FRAMECLAW_ROOT', 'FXDOCK_ROOT') || process.cwd();
    const aeRoot = envValue('SHOTWRIGHT_AE_ROOT', 'FRAMECLAW_AE_ROOT', 'FXDOCK_AE_ROOT') || 'C:\\Program Files\\Adobe\\Adobe After Effects 2026';
    const dataRoot = envValue('SHOTWRIGHT_DATA_ROOT', 'FRAMECLAW_DATA_ROOT', 'FXDOCK_DATA_ROOT') || path.win32.join(projectRoot, 'validation-data');
    const downloadRoot = envValue('SHOTWRIGHT_DOWNLOAD_ROOT') || path.win32.join(projectRoot, 'downloads');
    const downloadAllowedHosts = (envValue('SHOTWRIGHT_DOWNLOAD_ALLOWED_HOSTS') || '')
        .split(',')
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean);

    return {
        projectRoot,
        dockerBinary: envValue('SHOTWRIGHT_DOCKER', 'FRAMECLAW_DOCKER', 'FXDOCK_DOCKER') || 'docker',
        imageTag: envValue('SHOTWRIGHT_IMAGE', 'FRAMECLAW_IMAGE', 'FXDOCK_IMAGE') || 'shotwright:dev',
        validationContainerName: envValue('SHOTWRIGHT_VALIDATION_CONTAINER', 'FRAMECLAW_VALIDATION_CONTAINER', 'FXDOCK_VALIDATION_CONTAINER') || 'shotwright-validation',
        downloadRoot,
        downloadAllowedHosts,
        aeRoot,
        aeBinary: path.win32.join(aeRoot, 'Support Files', 'AfterFX.exe'),
        aeRenderBinary: path.win32.join(aeRoot, 'Support Files', 'aerender.exe'),
        dataRoot,
        workspaceMount: 'C:\\workspace',
        dataMount: 'C:\\data',
    };
}

export function validationPaths(config: ShotwrightConfig) {
    return {
        templateAep: path.win32.join(config.dataRoot, 'templates', 'validation_motion.aep'),
        outputMp4: path.win32.join(config.dataRoot, 'output', 'validation.mp4'),
        workRoot: path.win32.join(config.dataRoot, 'work'),
        createProjectScript: path.win32.join(config.projectRoot, 'scripts', 'create_validation_animation_project.jsx'),
        validationJob: path.win32.join(config.projectRoot, 'scripts', 'validation_nexrender_job.json'),
    };
}
