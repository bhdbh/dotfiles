"""Package the dependency-free local extension with the Python standard library."""
from pathlib import Path
import zipfile
import subprocess
import json

root = Path(__file__).parent
version = json.loads((root / "package.json").read_text())["version"]
build = root / "build"
build.mkdir(exist_ok=True)
protocol = "/usr/share/wayland-protocols/staging/ext-data-control/ext-data-control-v1.xml"
for kind, filename in [("client-header", "ext-data-control-v1.h"), ("private-code", "ext-data-control-v1.c")]:
    subprocess.run(["wayland-scanner", kind, protocol, str(build / filename)], check=True)
subprocess.run([
    "cc", "-D_GNU_SOURCE", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(build),
    str(root / "clipboard-owner.c"), str(build / "ext-data-control-v1.c"),
    "-lwayland-client", "-o", str(root / "clipboard-owner"),
], check=True)
manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
  <Metadata>
    <Identity Language="en-US" Id="yazi-file-actions" Version="{version}" Publisher="local" />
    <DisplayName>Yazi File Actions</DisplayName>
    <Description xml:space="preserve">Yazi deletion confirmation with native VS Code file actions preserved.</Description>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.100.0" />
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace" />
      <Property Id="Microsoft.VisualStudio.Code.ExecutesCode" Value="true" />
    </Properties>
  </Metadata>
  <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code" /></Installation>
  <Dependencies />
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />
  </Assets>
</PackageManifest>
'''
content_types = '''<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="json" ContentType="application/json" />
  <Default Extension="js" ContentType="application/javascript" />
  <Default Extension="py" ContentType="text/x-python" />
  <Default Extension="md" ContentType="text/markdown" />
  <Default Extension="vsixmanifest" ContentType="text/xml" />
</Types>
'''
package = root / f"yazi-file-actions-{version}.vsix"
with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("extension.vsixmanifest", manifest)
    archive.writestr("[Content_Types].xml", content_types)
    for name in ("package.json", "extension.js", "clipboard.py", "clipboard-owner"):
        archive.write(root / name, f"extension/{name}")
print(package)
