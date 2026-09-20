// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ImpactRegistry} from "../src/ImpactRegistry.sol";

interface VmEvidence {
    function envUint(string calldata name) external returns (uint256);
    function envAddress(string calldata name) external returns (address);
    function envBytes32(string calldata name) external returns (bytes32);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

/// @notice Registers the seeded invoice's commitment, signed by the operator.
/// @dev On a public network the backend holds no key, so this registration is made by the
/// operator's own wallet rather than by the API. The backend then reads the receipt back
/// and records it only if the event really commits this evidence -- see
/// `impactgraph.cli record-evidence-registration`. Idempotent: an already registered
/// evidence id is left alone, because the registry rejects duplicates.
contract RegisterSeedEvidence {
    VmEvidence private constant vm =
        VmEvidence(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external {
        uint256 operatorKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        ImpactRegistry registry = ImpactRegistry(vm.envAddress("IMPACT_REGISTRY_ADDRESS"));
        bytes32 evidenceId = vm.envBytes32("BOOTSTRAP_EVIDENCE_ID");
        bytes32 programId = vm.envBytes32("BOOTSTRAP_PROGRAM_ID");
        bytes32 commitment = vm.envBytes32("BOOTSTRAP_EVIDENCE_COMMITMENT");

        if (registry.entityExists(evidenceId)) {
            return;
        }
        vm.startBroadcast(operatorKey);
        registry.registerEvidence(evidenceId, programId, commitment);
        vm.stopBroadcast();
    }
}
