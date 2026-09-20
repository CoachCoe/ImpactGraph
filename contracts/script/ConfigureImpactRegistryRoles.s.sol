// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ImpactRegistry} from "../src/ImpactRegistry.sol";

interface VmRoles {
    function envUint(string calldata name) external returns (uint256);
    function envAddress(string calldata name) external returns (address);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

contract ConfigureImpactRegistryRoles {
    VmRoles private constant vm = VmRoles(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        ImpactRegistry registry = ImpactRegistry(vm.envAddress("IMPACT_REGISTRY_ADDRESS"));
        address operator = vm.envAddress("OPERATOR_WALLET_ADDRESS");
        address verifier = vm.envAddress("VERIFIER_WALLET_ADDRESS");
        require(operator != verifier, "operator and verifier must be distinct");

        vm.startBroadcast(deployerKey);
        registry.grantRole(registry.OPERATOR_ROLE(), operator);
        registry.grantRole(registry.VERIFIER_ROLE(), verifier);
        vm.stopBroadcast();
    }
}
