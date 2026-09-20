// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ImpactRegistry} from "../src/ImpactRegistry.sol";

interface VmBootstrap {
    function envUint(string calldata name) external returns (uint256);
    function envAddress(string calldata name) external returns (address);
    function envBytes32(string calldata name) external returns (bytes32);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// @notice Creates the program and claim entities the golden path commits against.
/// @dev Deployment alone leaves the registry empty, so registerEvidence reverts with
/// UnknownProgram and createAttestation with UnknownEntity. The identifiers and commitments
/// are computed by `impactgraph.cli chain-args`, because they use a framed encoding this
/// contract cannot reproduce. Idempotent: existing entities are left untouched.
contract BootstrapImpactRegistry {
    VmBootstrap private constant vm =
        VmBootstrap(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        ImpactRegistry registry = ImpactRegistry(vm.envAddress("IMPACT_REGISTRY_ADDRESS"));
        bytes32 programId = vm.envBytes32("BOOTSTRAP_PROGRAM_ID");
        bytes32 programCommitment = vm.envBytes32("BOOTSTRAP_PROGRAM_COMMITMENT");
        bytes32 claimId = vm.envBytes32("BOOTSTRAP_CLAIM_ID");
        bytes32 claimCommitment = vm.envBytes32("BOOTSTRAP_CLAIM_COMMITMENT");

        vm.startBroadcast(deployerKey);
        if (!registry.entityExists(programId)) {
            registry.createProgram(programId, programCommitment);
        }
        if (!registry.entityExists(claimId)) {
            registry.createClaim(claimId, programId, claimCommitment);
        }
        vm.stopBroadcast();
    }
}
