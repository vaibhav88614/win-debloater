# winget manifest (template)

These files package the app for the [Windows Package Manager](https://learn.microsoft.com/windows/package-manager/).
They are a **template** — the installer URL and hash are placeholders.

## Publishing checklist

1. Build and publish a GitHub Release (the `release` job in CI does this on a `v*` tag).
2. Compute the SHA256 of the released exe:
   ```pwsh
   (Get-FileHash dist/WinDebloater.exe -Algorithm SHA256).Hash
   ```
3. In `WinDebloater.WinDebloater.installer.yaml`:
   - set `InstallerUrl` to the release asset URL,
   - set `InstallerSha256` to the hash from step 2.
4. Bump `PackageVersion` in all three files to match the release.
5. Validate locally:
   ```pwsh
   winget validate --manifest tools/winget
   ```
6. Submit a PR to [microsoft/winget-pkgs](https://github.com/microsoft/winget-pkgs)
   under `manifests/w/WinDebloater/WinDebloater/<version>/`.

> Replace `OWNER` in the installer URL with the real GitHub org/user.
