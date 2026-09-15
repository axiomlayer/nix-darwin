{
  description = "Public, secret-free AxiomLayer Darwin compatibility fixture";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/c3eea5b2156db11c7eeeada3dc737711255b253e";

    nix-darwin = {
      url = "github:AxiomLayer/nix-darwin/c3e90c89649b07d1a96e4b9dd6cd0d6e44b91a74";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    { nixpkgs, nix-darwin, ... }:
    let
      mkFleet = system:
        nix-darwin.lib.darwinSystem {
          inherit system;
          modules = [ ./fleet-darwin.nix ];
        };

      configurations = {
        fleet-arm64 = mkFleet "aarch64-darwin";
        fleet-intel = mkFleet "x86_64-darwin";
      };

      mkContract = system: configuration:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          cfg = configuration.config;
        in
        pkgs.writeText "axiomlayer-darwin-contract-${system}.json" (builtins.toJSON {
          inherit system;
          caskRequireSha = cfg.homebrew.caskArgs.require_sha;
          computerName = cfg.networking.computerName;
          darwinRelease = cfg.system.darwinRelease;
          nixManagedByDarwin = cfg.nix.enable;
          primaryUser = cfg.system.primaryUser;
          runnerTokenPath = builtins.elemAt cfg.launchd.daemons.axiom-fleet-runner.serviceConfig.ProgramArguments 2;
          stateVersion = cfg.system.stateVersion;
          temporarySupervisor = cfg.launchd.user.agents.axiom-fleet-bootstrap.serviceConfig.Label;
        });
    in
    {
      darwinConfigurations = configurations;

      checks = {
        aarch64-darwin = {
          fleet-darwin = configurations.fleet-arm64.config.system.build.toplevel;
          fleet-contract = mkContract "aarch64-darwin" configurations.fleet-arm64;
        };
        x86_64-darwin = {
          fleet-darwin = configurations.fleet-intel.config.system.build.toplevel;
          fleet-contract = mkContract "x86_64-darwin" configurations.fleet-intel;
        };
      };
    };
}
