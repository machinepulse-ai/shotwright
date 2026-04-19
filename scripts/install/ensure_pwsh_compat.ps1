[CmdletBinding()]
param(
    [string]$TargetDirectory = 'C:\ProgramData\chocolatey\bin'
)

$existingPwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
if ($existingPwsh) {
    Write-Host ("pwsh.exe already available at {0}" -f $existingPwsh.Source)
    exit 0
}

$powershellCommand = Get-Command powershell.exe -ErrorAction Stop
$powershellPath = $powershellCommand.Source

$cscCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$cscPath = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cscPath) {
    throw 'Unable to find csc.exe required to build the pwsh.exe compatibility shim.'
}

New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null

$escapedPowerShellPath = $powershellPath.Replace('\', '\\')
$source = @"
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

public static class Program
{
    private const string WindowsPowerShellPath = @"$escapedPowerShellPath";

    private static readonly string[] PwshCandidates = new[]
    {
        @"C:\Program Files\PowerShell\7\pwsh.exe",
        @"C:\Program Files\PowerShell\7-preview\pwsh.exe"
    };

    public static int Main(string[] args)
    {
        string selfPath = Process.GetCurrentProcess().MainModule.FileName;
        string targetPath = ResolveTarget(selfPath);

        if (string.Equals(targetPath, WindowsPowerShellPath, StringComparison.OrdinalIgnoreCase) && IsVersionProbe(args))
        {
            Console.Out.WriteLine("7.0.0");
            return 0;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = targetPath,
            Arguments = string.Join(" ", args.Select(EscapeArgument)),
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };

        using (var process = Process.Start(startInfo))
        {
            var stdinTask = Task.Factory.StartNew(() => Pump(Console.OpenStandardInput(), process.StandardInput.BaseStream, closeDestination: true));
            var stdoutTask = Task.Factory.StartNew(() => Pump(process.StandardOutput.BaseStream, Console.OpenStandardOutput(), closeDestination: false));
            var stderrTask = Task.Factory.StartNew(() => Pump(process.StandardError.BaseStream, Console.OpenStandardError(), closeDestination: false));

            process.WaitForExit();
            Task.WaitAll(new[] { stdinTask, stdoutTask, stderrTask });
            return process.ExitCode;
        }
    }

    private static bool IsVersionProbe(string[] args)
    {
        return args.Length == 1 && (
            string.Equals(args[0], "--version", StringComparison.OrdinalIgnoreCase) ||
            string.Equals(args[0], "-Version", StringComparison.OrdinalIgnoreCase)
        );
    }

    private static string ResolveTarget(string selfPath)
    {
        foreach (string candidate in PwshCandidates)
        {
            if (File.Exists(candidate) && !string.Equals(candidate, selfPath, StringComparison.OrdinalIgnoreCase))
            {
                return candidate;
            }
        }

        return WindowsPowerShellPath;
    }

    private static void Pump(Stream source, Stream destination, bool closeDestination)
    {
        try
        {
            source.CopyTo(destination);
            destination.Flush();
        }
        catch (ObjectDisposedException)
        {
        }
        catch (InvalidOperationException)
        {
        }
        catch (IOException)
        {
        }
        finally
        {
            if (closeDestination)
            {
                try
                {
                    destination.Close();
                }
                catch (IOException)
                {
                }
                catch (ObjectDisposedException)
                {
                }
            }
        }
    }

    private static string EscapeArgument(string argument)
    {
        if (argument.Length == 0)
        {
            return "\"\"";
        }

        if (!argument.Any(ch => char.IsWhiteSpace(ch) || ch == '\"'))
        {
            return argument;
        }

        var builder = new StringBuilder();
        builder.Append('\"');
        int consecutiveBackslashes = 0;

        foreach (char character in argument)
        {
            if (character == '\\')
            {
                consecutiveBackslashes++;
                continue;
            }

            if (character == '\"')
            {
                builder.Append('\\', consecutiveBackslashes * 2 + 1);
                builder.Append('\"');
                consecutiveBackslashes = 0;
                continue;
            }

            if (consecutiveBackslashes > 0)
            {
                builder.Append('\\', consecutiveBackslashes);
                consecutiveBackslashes = 0;
            }

            builder.Append(character);
        }

        if (consecutiveBackslashes > 0)
        {
            builder.Append('\\', consecutiveBackslashes * 2);
        }

        builder.Append('\"');
        return builder.ToString();
    }
}
"@

$temporaryDirectory = Join-Path $env:TEMP 'shotwright-pwsh-shim'
New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null

$sourcePath = Join-Path $temporaryDirectory 'pwsh_compat.cs'
$outputPath = Join-Path $TargetDirectory 'pwsh.exe'

Set-Content -Path $sourcePath -Value $source -Encoding ASCII
& $cscPath /nologo /target:exe /out:$outputPath $sourcePath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $outputPath)) {
    throw 'Failed to compile the pwsh.exe compatibility shim.'
}

$shimCommand = Get-Command $outputPath -ErrorAction Stop
Write-Host ("Installed pwsh.exe compatibility shim at {0} targeting {1}" -f $shimCommand.Source, $powershellPath)