// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

import {ImpactRegistry} from "../src/ImpactRegistry.sol";

contract Actor {
    function createProgram(ImpactRegistry r, bytes32 id, bytes32 hash) external {
        r.createProgram(id, hash);
    }

    function registerEvidence(ImpactRegistry r, bytes32 id, bytes32 p, bytes32 hash) external {
        r.registerEvidence(id, p, hash);
    }

    function attest(
        ImpactRegistry r,
        bytes32 id,
        bytes32 subject,
        ImpactRegistry.AttestationType kind,
        bytes32 statement,
        bytes32 bundle
    ) external {
        r.createAttestation(id, subject, kind, statement, bundle);
    }

    function revoke(ImpactRegistry r, bytes32 id, bytes32 reason) external {
        r.revokeAttestation(id, reason);
    }

    function link(
        ImpactRegistry r,
        bytes32 source,
        ImpactRegistry.Relationship relationship,
        bytes32 target
    ) external {
        r.linkProvenance(source, relationship, target);
    }
}

contract ImpactRegistryTest {
    ImpactRegistry internal registry;
    Actor internal operator;
    Actor internal verifier;
    Actor internal stranger;
    bytes32 internal constant PROGRAM = keccak256("program");
    bytes32 internal constant CLAIM = keccak256("claim");

    function setUp() public {
        registry = new ImpactRegistry(address(this));
        operator = new Actor();
        verifier = new Actor();
        stranger = new Actor();
        registry.grantRole(registry.OPERATOR_ROLE(), address(operator));
        registry.grantRole(registry.VERIFIER_ROLE(), address(verifier));
        operator.createProgram(registry, PROGRAM, keccak256("program commitment"));
        registry.grantRole(registry.OPERATOR_ROLE(), address(this));
        registry.createClaim(CLAIM, PROGRAM, keccak256("claim payload"));
    }

    function testOperatorCanRegisterEvidenceOnlyOnce() public {
        bytes32 id = keccak256("evidence");
        operator.registerEvidence(registry, id, PROGRAM, keccak256("original bytes"));
        (ImpactRegistry.EntityType kind,,,,) = registry.entities(id);
        require(kind == ImpactRegistry.EntityType.EVIDENCE, "wrong kind");
        try operator.registerEvidence(registry, id, PROGRAM, keccak256("replacement")) {
            revert("duplicate accepted");
        } catch (bytes memory reason) {
            require(
                _selector(reason) == ImpactRegistry.EvidenceAlreadyRegistered.selector,
                "wrong error"
            );
        }
    }

    function testOperatorCannotIssueIndependentVerification() public {
        try operator.attest(
            registry,
            keccak256("bad"),
            CLAIM,
            ImpactRegistry.AttestationType.INDEPENDENT_VERIFIER,
            keccak256("statement"),
            keccak256("bundle")
        ) {
            revert("operator impersonated verifier");
        } catch (bytes memory reason) {
            require(
                _selector(reason) == ImpactRegistry.UnauthorizedAttestor.selector, "wrong error"
            );
        }
    }

    function testOperatorAndVerifierRolesCannotOverlap() public {
        try registry.grantRole(registry.VERIFIER_ROLE(), address(operator)) {
            revert("overlapping role accepted");
        } catch (bytes memory reason) {
            require(_selector(reason) == ImpactRegistry.ConflictingRole.selector, "wrong error");
        }
    }

    function testIndependentVerificationRequiresABundle() public {
        try verifier.attest(
            registry,
            keccak256("empty-bundle"),
            CLAIM,
            ImpactRegistry.AttestationType.INDEPENDENT_VERIFIER,
            keccak256("statement"),
            bytes32(0)
        ) {
            revert("empty bundle accepted");
        } catch (bytes memory reason) {
            require(
                _selector(reason) == ImpactRegistry.VerificationBundleRequired.selector,
                "wrong error"
            );
        }
    }

    function testOperatorAttestationCannotVerifyAClaim() public {
        bytes32 id = keccak256("operator-attestation");
        operator.attest(
            registry,
            id,
            CLAIM,
            ImpactRegistry.AttestationType.OPERATOR,
            keccak256("statement"),
            keccak256("bundle")
        );
        try operator.link(registry, id, ImpactRegistry.Relationship.VERIFIES, CLAIM) {
            revert("operator attestation verified claim");
        } catch (bytes memory reason) {
            require(
                _selector(reason) == ImpactRegistry.InvalidAttestationRelationship.selector,
                "wrong error"
            );
        }
    }

    function testVerifierAttestationIsAppendOnlyAndRevocable() public {
        bytes32 id = keccak256("attestation");
        verifier.attest(
            registry,
            id,
            CLAIM,
            ImpactRegistry.AttestationType.INDEPENDENT_VERIFIER,
            keccak256("statement"),
            keccak256("bundle")
        );
        (bytes32 subject,, address issuer,,,, bool revoked) = registry.attestations(id);
        require(subject == CLAIM && issuer == address(verifier) && !revoked, "attestation mismatch");
        verifier.revoke(registry, id, keccak256("withdrawn"));
        (,,,,,, revoked) = registry.attestations(id);
        require(revoked, "not revoked");
    }

    function testUnknownSourceAndInvalidRelationshipRevert() public {
        try registry.linkProvenance(
            keccak256("unknown"), ImpactRegistry.Relationship.SUPPORTS, CLAIM
        ) {
            revert("unknown accepted");
        } catch (bytes memory reason) {
            require(_selector(reason) == ImpactRegistry.UnknownEntity.selector, "wrong error");
        }
        bytes32 evidence = keccak256("evidence2");
        operator.registerEvidence(registry, evidence, PROGRAM, keccak256("bytes"));
        try registry.linkProvenance(evidence, ImpactRegistry.Relationship.PAYS, CLAIM) {
            revert("invalid relationship accepted");
        } catch (bytes memory reason) {
            require(
                _selector(reason) == ImpactRegistry.InvalidProvenanceRelationship.selector,
                "wrong error"
            );
        }
    }

    function _selector(bytes memory reason) internal pure returns (bytes4 selector) {
        require(reason.length >= 4, "missing revert data");
        assembly {
            selector := mload(add(reason, 32))
        }
    }
}
