// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ImpactRegistry} from "../src/ImpactRegistry.sol";

interface Vm {
    function envUint(string calldata name) external returns (uint256);
    function addr(uint256 privateKey) external returns (address);
    function startBroadcast(uint256 privateKey) external;
    function stopBroadcast() external;
}

contract DeployImpactRegistry {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external returns (ImpactRegistry registry) {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);
        vm.startBroadcast(deployerKey);
        registry = new ImpactRegistry(deployer);
        registry.grantRole(registry.OPERATOR_ROLE(), deployer);
        vm.stopBroadcast();
    }
}

