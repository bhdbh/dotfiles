{
  inputs,
  lib,
  # Common dependencies for the plugin
  glib,
  makeWrapper,
  rustPlatform,
  atk,
  gtk3,
  gtk-layer-shell,
  pkg-config,
  librsvg,
  cargo,
  rustc,
  # Generic args
  name,
  lockFile,
  extraInputs ? [ ], # allow appending buildInputs
  ...
}:
let
  cargoToml = builtins.fromTOML (builtins.readFile ../../plugins/${name}/Cargo.toml);
  pname = cargoToml.package.name;
  version = cargoToml.package.version;
in
rustPlatform.buildRustPackage {
  inherit pname version;

  # The maintained IPC and Kidex client crates live beside this workspace.
  src = lib.fileset.toSource {
    root = ../../..;
    fileset = lib.fileset.unions [
      ../../../anyrun
      ../../../anyrun-prefix-provider
      ../../../kidex
    ];
  };
  cargoLock = {
    inherit lockFile;
    # Temporary while packages aren't yet stabilized
    allowBuiltinFetchGit = true;
  };

  strictDeps = true;

  nativeBuildInputs = [
    pkg-config
    makeWrapper
  ];

  buildInputs = [
    glib
    atk
    gtk3
    librsvg
    gtk-layer-shell
  ]
  ++ extraInputs;

  doCheck = true;
  checkInputs = [
    cargo
    rustc
  ];

  copyLibs = true;
  cargoBuildFlags = [ "-p ${name}" ];
  cargoRoot = "anyrun";
  cargoTestFlags = [ "-p ${name}" ];

  CARGO_BUILD_INCREMENTAL = "false";
  RUST_BACKTRACE = "full";

  meta = {
    description = "The ${name} plugin for Anyrun";
    homepage = "https://github.com/anyrun-org/anyrun";
    license = [ lib.licenses.gpl3 ];
    maintainers = with lib.maintainers; [
      NotAShelf
      n3oney
    ];
  };
}
