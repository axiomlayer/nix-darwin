{ lib, pkgs, ... }:

let
  bootstrapSupervisor = pkgs.writeShellScript "axiom-fleet-bootstrap-supervisor" ''
    set -eu
    printf '%s\n' 'fabricated bootstrap supervisor: evaluation and build only'
  '';
  runnerAdapter = pkgs.writeShellScript "axiom-fleet-runner-adapter" ''
    set -eu
    printf '%s\n' 'fabricated runner adapter: enrollment is host-only'
  '';
in
{
  assertions = [
    {
      assertion = lib.hasPrefix "/run/" "/run/axiom-ci/fabricated-runner-token";
      message = "The compatibility fixture must keep its fabricated token path outside the Nix store.";
    }
  ];

  # Stage-nine Nix is delivered by the runtime foundation. nix-darwin owns
  # declarative convergence, not installation or replacement of Nix itself.
  nix.enable = false;

  system = {
    primaryUser = "fleet-ci";
    stateVersion = 7;
    activationScripts.postActivation.text = lib.mkAfter ''
      echo >&2 "axiom fleet post-activation hook declared; CI never executes it"
    '';
  };

  users.users.fleet-ci.home = "/Users/fleet-ci";

  networking = {
    hostName = "fleet-darwin-ci";
    localHostName = "fleet-darwin-ci";
    computerName = "AxiomLayer Darwin CI";
  };

  environment = {
    systemPackages = [ pkgs.jq ];
    etc."axiom-fleet/integration.json".text = builtins.toJSON {
      schemaVersion = 1;
      credentialMode = "external-adapter";
      enrollment = "fabricated-ci-only";
      activation = "host-only";
    };
  };

  programs.zsh.enable = true;

  homebrew = {
    enable = true;
    user = "fleet-ci";
    taps = [ ];
    brews = [ ];
    caskArgs.require_sha = true;
    greedyCasks = true;
    casks = [
      {
        name = "visual-studio-code";
        args.require_sha = true;
        greedy = true;
      }
      {
        name = "zed";
        args.require_sha = true;
        greedy = true;
      }
      {
        name = "ghostty";
        args.require_sha = true;
        greedy = true;
      }
    ];
    masApps = { };
    onActivation = {
      autoUpdate = false;
      upgrade = false;
      cleanup = "none";
    };
    global = {
      autoUpdate = false;
      brewfile = true;
    };
    enableBashIntegration = false;
    enableFishIntegration = false;
    enableZshIntegration = false;
  };

  system.defaults = {
    NSGlobalDomain = {
      ApplePressAndHoldEnabled = false;
      AppleShowAllExtensions = true;
      NSAutomaticCapitalizationEnabled = false;
      NSAutomaticDashSubstitutionEnabled = false;
      NSAutomaticQuoteSubstitutionEnabled = false;
      NSAutomaticSpellingCorrectionEnabled = false;
    };
    dock = {
      autohide = true;
      show-recents = false;
    };
    finder = {
      AppleShowAllExtensions = true;
      FXPreferredViewStyle = "Nlsv";
    };
  };

  # These jobs prove that nix-darwin can render the fleet's temporary reboot
  # supervisor and permanent self-runner adapter. They do not enroll or run.
  launchd.user.agents.axiom-fleet-bootstrap.serviceConfig = {
    Label = "com.axiomlayer.fleet-bootstrap";
    ProgramArguments = [ bootstrapSupervisor ];
    RunAtLoad = true;
    KeepAlive = false;
  };

  launchd.daemons.axiom-fleet-runner.serviceConfig = {
    Label = "com.axiomlayer.fleet-runner";
    ProgramArguments = [
      runnerAdapter
      "--token-file"
      "/run/axiom-ci/fabricated-runner-token"
      "--runner-group"
      "fleet-trusted"
    ];
    RunAtLoad = true;
    KeepAlive = true;
  };
}
