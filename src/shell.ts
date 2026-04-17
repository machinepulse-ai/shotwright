import { spawn } from 'node:child_process';

export interface CommandResult {
    command: string;
    args: string[];
    code: number;
    stdout: string;
    stderr: string;
}

export interface CommandOptions {
    cwd?: string;
    env?: NodeJS.ProcessEnv;
    allowNonZero?: boolean;
}

export async function runCommand(command: string, args: string[], options: CommandOptions = {}): Promise<CommandResult> {
    return await new Promise<CommandResult>((resolve, reject) => {
        const child = spawn(command, args, {
            cwd: options.cwd,
            env: options.env,
            shell: false,
            windowsHide: true,
        });

        let stdout = '';
        let stderr = '';

        child.stdout.on('data', (chunk: Buffer | string) => {
            stdout += String(chunk);
        });

        child.stderr.on('data', (chunk: Buffer | string) => {
            stderr += String(chunk);
        });

        child.on('error', reject);
        child.on('close', (code: number | null) => {
            const result: CommandResult = {
                command,
                args,
                code: code ?? 0,
                stdout,
                stderr,
            };

            if (!options.allowNonZero && result.code !== 0) {
                reject(new Error(`${command} ${args.join(' ')} failed with code ${result.code}\n${stderr || stdout}`));
                return;
            }

            resolve(result);
        });
    });
}
