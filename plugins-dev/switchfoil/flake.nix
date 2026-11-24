{
  description = "rom-hub-switchfoil, a ROM Hub plugin for Tinfoil and CyberFoil shops";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # The plugin runs inside the ROM Hub host, so development and CI use the
    # same host revision ROMarr's Docker image pins.
    rom-hub = {
      url = "github:BlizzHacker/rom-hub/3f5180aa336b0c192b86877ea6f3dc59a8075631";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      rom-hub,
    }:
    let
      lib = nixpkgs.lib;

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];

      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # rom-hub declares requires-python >= 3.12.
      pythonVersions = [
        "python312"
        "python313"
      ];

      defaultPython = "python312";

      pluginSource = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions [
          ./manifest.toml
          ./switchfoil
          ./tests
          ./tools
        ];
      };

      pythonPackagesOverlay = pkgs: final: prev: {
        pyseccomp = final.buildPythonPackage rec {
          pname = "pyseccomp";
          version = "0.1.2";
          pyproject = true;
          src = final.fetchPypi {
            inherit pname version;
            hash = "sha256-DVTvrPcb2kvf0hIz9URLcwy+z1k9YC95bmQSqGgiWL0=";
          };
          build-system = [ final.setuptools ];
          # pyseccomp finds libseccomp with ctypes.util.find_library, which
          # finds nothing inside a Nix build or a Nix-built environment.
          postPatch = lib.optionalString pkgs.stdenv.hostPlatform.isLinux ''
            substituteInPlace pyseccomp.py \
              --replace-fail 'ctypes.util.find_library("seccomp")' \
              '"${lib.getLib pkgs.libseccomp}/lib/libseccomp.so.2"'
          '';
          doCheck = false;
          meta.license = lib.licenses.mit;
        };

        rom-hub = final.buildPythonPackage {
          pname = "rom-hub";
          version = "0.1.0";
          pyproject = true;
          src = rom-hub;
          build-system = [ final.setuptools ];
          dependencies = [
            final.pydantic
            final.httpx
            final.python-socketio
          ]
          ++ (final.python-socketio.optional-dependencies.client or [ ])
          ++ lib.optional final.stdenv.hostPlatform.isLinux final.pyseccomp;
          # rom-hub's own suite spawns sandboxed workers and belongs to its CI.
          doCheck = false;
          pythonImportsCheck = [
            "rom_hub"
            "rom_hub_sdk"
          ];
        };
      };

      pythonFor =
        pkgs: name:
        pkgs.${name}.override {
          packageOverrides = pythonPackagesOverlay pkgs;
        };
    in
    {
      packages = forAllSystems (
        pkgs:
        lib.listToAttrs (
          map (name: {
            name = "rom-hub-${name}";
            value = (pythonFor pkgs name).pkgs.rom-hub;
          }) pythonVersions
        )
      );

      # Each check runs the plugin's own suite against the pinned rom-hub.
      checks = forAllSystems (
        pkgs:
        lib.listToAttrs (
          map (name: {
            name = "tests-${name}";
            value =
              pkgs.runCommand "rom-hub-switchfoil-tests-${name}"
                {
                  nativeBuildInputs = [ ((pythonFor pkgs name).withPackages (ps: [ ps.rom-hub ])) ];
                  # The fixture shop test binds 127.0.0.1 only.
                  __darwinAllowLocalNetworking = true;
                }
                ''
                  # httpx loads a CA bundle even for plain http, and the build
                  # sandbox points SSL_CERT_FILE at a file that does not exist.
                  export SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt
                  cp -r ${pluginSource}/. .
                  chmod -R u+w .
                  python3 -m unittest discover -s tests -v
                  touch $out
                '';
          }) pythonVersions
        )
      );

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            ((pythonFor pkgs defaultPython).withPackages (ps: [ ps.rom-hub ]))
            pkgs.just
            pkgs.ruff
            pkgs.gh
            pkgs.nixfmt
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);
    };
}
