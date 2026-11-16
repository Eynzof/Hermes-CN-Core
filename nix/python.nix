# nix/python.nix — uv2nix virtual environment builder
{
  lib,
  callPackage,
  fetchurl,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,
  stdenv,
  # Filtered Python source (see lib.nix pythonSrc) — keeps JS/docs/skills
  # edits from invalidating the venv derivation.
  pythonSrc,
  dependency-groups ? [ "all" ],
}:
let
  # The interpreter family comes from pm/lock.json (pythonLock.nix owns the
  # selection); every override below must be built for THAT interpreter.
  pythonLock = callPackage ./pythonLock.nix { };
  python = pythonLock.interpreter;
  pythonPackages = python.pkgs;

  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = pythonSrc; };
  hacks = callPackage pyproject-nix.build.hacks { };

  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };

  isAarch64Darwin = stdenv.hostPlatform.system == "aarch64-darwin";

  # Keep the workspace locked through uv2nix, but supply the local voice stack
  # from nixpkgs so wheel-only transitive artifacts do not break evaluation.
  mkPrebuiltPassthru = dependencies: {
    inherit dependencies;
    optional-dependencies = { };
    dependency-groups = { };
  };

  mkPrebuiltOverride =
    final: from: dependencies:
    hacks.nixpkgsPrebuilt {
      inherit from;
      prev = {
        nativeBuildInputs = [ final.pyprojectHook ];
        passthru = mkPrebuiltPassthru dependencies;
      };
    };

  # Legacy packages that ship only sdists with setup.py/setup.cfg and no
  # pyproject.toml need setuptools supplied explicitly as a build dep.
  buildSystemOverrides =
    final: prev:
    builtins.mapAttrs
      (
        name: _:
        prev.${name}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ final.setuptools ];
        })
      )
      (
        lib.genAttrs [
          "alibabacloud-credentials-api"
          "alibabacloud-endpoint-util"
          "alibabacloud-gateway-dingtalk"
          "alibabacloud-gateway-spi"
          "alibabacloud-tea"
          "ciso8601"
          "ripgrepy"
        ] (_: null)
      )
    // {
      # Python 3.15 builds httptools from source. Its backend explicitly caps
      # setuptools at 82.0.1; keep that build-only pin out of the runtime set.
      httptools = prev.httptools.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [
          (final.setuptools.overrideAttrs (_: {
            version = "82.0.1";
            src = fetchurl {
              url = "https://files.pythonhosted.org/packages/9d/76/f789f7a86709c6b087c5a2f52f911838cad707cc613162401badc665acfe/setuptools-82.0.1-py3-none-any.whl";
              hash = "sha256-pZ42JlLwjc1HfHi7bnvZ2Ap5lbxzzncwUCKKNIzi5bs=";
            };
          }))
        ];
      });
      # Its locked wheels stop at Python 3.14. Match the sdist's declared
      # backend requirements when uv2nix builds it for Python 3.15.
      zstandard = prev.zstandard.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
          setuptools = [ ];
          packaging = [ ];
          cffi = [ ];
        };
      });
      lz4 = prev.lz4.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
          setuptools = [ ];
          wheel = [ ];
          setuptools-scm = [ ];
          pkgconfig = [ ];
        };
      });
      ruamel-yaml-clib = prev.ruamel-yaml-clib.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
          setuptools = [ ];
          wheel = [ ];
        };
      });
      # The locked sdist has no build-system metadata; setup.py imports
      # setuptools and uses CFFI to compile the bundled libolm.
      python-olm = prev.python-olm.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
          setuptools = [ ];
          cffi = [ ];
        };
      });
      # [kittentts] locks misaki as a git source. uv.lock records no build
      # backend for it, so supply the hatchling its pyproject declares.
      misaki = prev.misaki.overrideAttrs (old: {
        nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ final.resolveBuildSystem {
          hatchling = [ ];
        };
      });
    };

  pythonPackageOverrides =
    final: _prev:
    if isAarch64Darwin then
      {
        numpy = mkPrebuiltOverride final pythonPackages.numpy { };

        pyarrow = mkPrebuiltOverride final pythonPackages.pyarrow { };

        av = mkPrebuiltOverride final pythonPackages.av { };

        humanfriendly = mkPrebuiltOverride final pythonPackages.humanfriendly { };

        coloredlogs = mkPrebuiltOverride final pythonPackages.coloredlogs {
          humanfriendly = [ ];
        };

        onnxruntime = mkPrebuiltOverride final pythonPackages.onnxruntime {
          coloredlogs = [ ];
          numpy = [ ];
          packaging = [ ];
        };

        ctranslate2 = mkPrebuiltOverride final pythonPackages.ctranslate2 {
          numpy = [ ];
          pyyaml = [ ];
        };

        faster-whisper = mkPrebuiltOverride final pythonPackages.faster-whisper {
          av = [ ];
          ctranslate2 = [ ];
          huggingface-hub = [ ];
          onnxruntime = [ ];
          tokenizers = [ ];
          tqdm = [ ];
        };
      }
    else
      { };

  pythonSet =
    (callPackage pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          buildSystemOverrides
          pythonPackageOverrides
          # ``setup.py`` permits wheel/sdist creation only from the sealed
          # Hermes derivation. This is deliberately a derivation environment
          # variable, not a devShell variable: ``nix develop -c uv build``
          # must remain blocked.
          (final: prev: {
            hermes-agent = prev.hermes-agent.overrideAttrs (_old: {
              HERMES_NIX_BUILD = "1";
            });
          })
        ]
      );

  # The editable venv points at the live checkout, so it uses an
  # UNFILTERED workspace rooted at a real path — mkEditablePyprojectOverlay
  # computes relative paths via lib.path.splitRoot, which rejects the
  # filtered pythonSrc (a cleanSourceWith set, not a path).  Filtering
  # buys nothing here anyway: the editable install reads from
  # $HERMES_PYTHON_SRC_ROOT at runtime.
  workspaceRoot = ./..;
  editableWorkspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };
  editableOverlay = editableWorkspace.mkEditablePyprojectOverlay {
    root = "$HERMES_PYTHON_SRC_ROOT"; # resolved at shellHook time
  };

  editableSet = pythonSet.overrideScope (
    lib.composeManyExtensions [
      editableOverlay
      (final: prev: {
        hermes-agent = prev.hermes-agent.overrideAttrs (old: {
          # point straight at the real source instead of the filtered nix store copy
          src = workspaceRoot;
          nativeBuildInputs = old.nativeBuildInputs ++ final.resolveBuildSystem { editables = [ ]; };
        });
      })
    ]
  );
in
{
  inherit python;

  venv = pythonSet.mkVirtualEnv "hermes-agent-env" {
    hermes-agent = dependency-groups;
  };
  editableVenv = editableSet.mkVirtualEnv "hermes-agent-editable-env" {
    hermes-agent = dependency-groups;
  };
}
