// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @title ImpactRegistry
/// @notice Immutable registry of compact impact provenance commitments and attestations.
/// @dev Evidence contents and personal information must never be supplied to this contract.
contract ImpactRegistry {
    bytes32 public constant ADMIN_ROLE = keccak256("ADMIN");
    bytes32 public constant OPERATOR_ROLE = keccak256("OPERATOR");
    bytes32 public constant VERIFIER_ROLE = keccak256("VERIFIER");

    enum EntityType {
        NONE,
        PROGRAM,
        FUNDING,
        ALLOCATION,
        FINANCIAL_TRANSACTION,
        DELIVERY,
        EVIDENCE,
        OUTCOME,
        CLAIM,
        ATTESTATION
    }

    enum Relationship {
        FUNDS,
        ALLOCATES_TO,
        PAYS,
        SUPPORTS,
        EVIDENCES,
        DELIVERS,
        ATTESTS,
        VERIFIES,
        PRODUCES,
        SUPERSEDES
    }

    enum AttestationType {
        OPERATOR,
        INDEPENDENT_VERIFIER
    }

    struct Entity {
        EntityType entityType;
        bytes32 commitment;
        bytes32 programId;
        address recordedBy;
        uint64 recordedAt;
    }

    struct Attestation {
        bytes32 subjectId;
        AttestationType attestationType;
        address issuer;
        bytes32 statementHash;
        bytes32 verificationBundleHash;
        uint64 createdAt;
        bool revoked;
    }

    error Unauthorized(address caller, bytes32 requiredRole);
    error DuplicateEntity(bytes32 entityId);
    error UnknownEntity(bytes32 entityId);
    error UnknownProgram(bytes32 programId);
    error EvidenceAlreadyRegistered(bytes32 evidenceId);
    error UnauthorizedAttestor(address caller);
    error InvalidProvenanceRelationship(
        EntityType sourceType, Relationship relationship, EntityType targetType
    );
    error DuplicateProvenance(bytes32 edgeId);
    error UnknownAttestation(bytes32 attestationId);
    error AttestationAlreadyRevoked(bytes32 attestationId);
    error InvalidIdentifier();
    error ConflictingRole(address account, bytes32 existingRole, bytes32 requestedRole);
    error VerificationBundleRequired();
    error AttestationSubjectMismatch(bytes32 attestationId, bytes32 targetId);
    error InvalidAttestationRelationship(bytes32 attestationId, Relationship relationship);

    event RoleGranted(bytes32 indexed role, address indexed account, address indexed sender);
    event ProgramCreated(bytes32 indexed programId, bytes32 commitment, address indexed operator);
    event FundingRecorded(bytes32 indexed fundingId, bytes32 indexed programId, bytes32 commitment);
    event AllocationRecorded(
        bytes32 indexed allocationId, bytes32 indexed programId, bytes32 commitment
    );
    event FinancialTransactionRecorded(
        bytes32 indexed financialTransactionId, bytes32 indexed programId, bytes32 commitment
    );
    event DeliveryRecorded(
        bytes32 indexed deliveryId, bytes32 indexed programId, bytes32 commitment
    );
    event EvidenceRegistered(
        bytes32 indexed evidenceId, bytes32 indexed programId, bytes32 contentHash
    );
    event OutcomeRecorded(bytes32 indexed outcomeId, bytes32 indexed programId, bytes32 commitment);
    event ClaimCreated(bytes32 indexed claimId, bytes32 indexed programId, bytes32 claimHash);
    event AttestationCreated(
        bytes32 indexed attestationId,
        bytes32 indexed subjectId,
        address indexed issuer,
        AttestationType attestationType,
        bytes32 statementHash,
        bytes32 verificationBundleHash
    );
    event AttestationRevoked(
        bytes32 indexed attestationId, address indexed revokedBy, bytes32 reasonHash
    );
    event ProvenanceLinked(
        bytes32 indexed edgeId,
        bytes32 indexed sourceId,
        bytes32 indexed targetId,
        Relationship relationship
    );

    mapping(bytes32 => mapping(address => bool)) public hasRole;
    mapping(bytes32 => Entity) public entities;
    mapping(bytes32 => Attestation) public attestations;
    mapping(bytes32 => bool) public provenanceEdges;

    constructor(address initialAdmin) {
        if (initialAdmin == address(0)) revert InvalidIdentifier();
        hasRole[ADMIN_ROLE][initialAdmin] = true;
        emit RoleGranted(ADMIN_ROLE, initialAdmin, initialAdmin);
    }

    modifier onlyRole(bytes32 role) {
        if (!hasRole[role][msg.sender]) revert Unauthorized(msg.sender, role);
        _;
    }

    function grantRole(bytes32 role, address account) external onlyRole(ADMIN_ROLE) {
        if (account == address(0)) revert InvalidIdentifier();
        if (role == VERIFIER_ROLE && hasRole[OPERATOR_ROLE][account]) {
            revert ConflictingRole(account, OPERATOR_ROLE, VERIFIER_ROLE);
        }
        if (role == OPERATOR_ROLE && hasRole[VERIFIER_ROLE][account]) {
            revert ConflictingRole(account, VERIFIER_ROLE, OPERATOR_ROLE);
        }
        hasRole[role][account] = true;
        emit RoleGranted(role, account, msg.sender);
    }

    function createProgram(bytes32 id, bytes32 commitment) external onlyRole(OPERATOR_ROLE) {
        _record(id, EntityType.PROGRAM, commitment, id);
        emit ProgramCreated(id, commitment, msg.sender);
    }

    function recordFunding(bytes32 id, bytes32 programId, bytes32 commitment)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.FUNDING, programId, commitment);
        emit FundingRecorded(id, programId, commitment);
    }

    function recordAllocation(bytes32 id, bytes32 programId, bytes32 commitment)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.ALLOCATION, programId, commitment);
        emit AllocationRecorded(id, programId, commitment);
    }

    function recordFinancialTransaction(bytes32 id, bytes32 programId, bytes32 commitment)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.FINANCIAL_TRANSACTION, programId, commitment);
        emit FinancialTransactionRecorded(id, programId, commitment);
    }

    function recordDelivery(bytes32 id, bytes32 programId, bytes32 commitment)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.DELIVERY, programId, commitment);
        emit DeliveryRecorded(id, programId, commitment);
    }

    function registerEvidence(bytes32 id, bytes32 programId, bytes32 contentHash)
        external
        onlyRole(OPERATOR_ROLE)
    {
        if (entities[id].entityType != EntityType.NONE) {
            revert EvidenceAlreadyRegistered(id);
        }
        _recordForProgram(id, EntityType.EVIDENCE, programId, contentHash);
        emit EvidenceRegistered(id, programId, contentHash);
    }

    function recordOutcome(bytes32 id, bytes32 programId, bytes32 commitment)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.OUTCOME, programId, commitment);
        emit OutcomeRecorded(id, programId, commitment);
    }

    function createClaim(bytes32 id, bytes32 programId, bytes32 claimHash)
        external
        onlyRole(OPERATOR_ROLE)
    {
        _recordForProgram(id, EntityType.CLAIM, programId, claimHash);
        emit ClaimCreated(id, programId, claimHash);
    }

    function createAttestation(
        bytes32 id,
        bytes32 subjectId,
        AttestationType attestationType,
        bytes32 statementHash,
        bytes32 verificationBundleHash
    ) external {
        if (entities[subjectId].entityType == EntityType.NONE) {
            revert UnknownEntity(subjectId);
        }
        bytes32 requiredRole =
            attestationType == AttestationType.OPERATOR ? OPERATOR_ROLE : VERIFIER_ROLE;
        if (!hasRole[requiredRole][msg.sender]) revert UnauthorizedAttestor(msg.sender);
        if (
            attestationType == AttestationType.INDEPENDENT_VERIFIER
                && verificationBundleHash == bytes32(0)
        ) revert VerificationBundleRequired();
        _record(id, EntityType.ATTESTATION, statementHash, entities[subjectId].programId);
        attestations[id] = Attestation(
            subjectId,
            attestationType,
            msg.sender,
            statementHash,
            verificationBundleHash,
            uint64(block.timestamp),
            false
        );
        emit AttestationCreated(
            id, subjectId, msg.sender, attestationType, statementHash, verificationBundleHash
        );
    }

    function revokeAttestation(bytes32 id, bytes32 reasonHash) external {
        Attestation storage attestation = attestations[id];
        if (attestation.createdAt == 0) revert UnknownAttestation(id);
        if (attestation.revoked) revert AttestationAlreadyRevoked(id);
        if (msg.sender != attestation.issuer && !hasRole[ADMIN_ROLE][msg.sender]) {
            revert UnauthorizedAttestor(msg.sender);
        }
        attestation.revoked = true;
        emit AttestationRevoked(id, msg.sender, reasonHash);
    }

    function linkProvenance(bytes32 sourceId, Relationship relationship, bytes32 targetId)
        external
        onlyRole(OPERATOR_ROLE)
        returns (bytes32 edgeId)
    {
        EntityType sourceType = entities[sourceId].entityType;
        EntityType targetType = entities[targetId].entityType;
        if (sourceType == EntityType.NONE) revert UnknownEntity(sourceId);
        if (targetType == EntityType.NONE) revert UnknownEntity(targetId);
        if (sourceType == EntityType.ATTESTATION) {
            Attestation storage attestation = attestations[sourceId];
            if (attestation.subjectId != targetId) {
                revert AttestationSubjectMismatch(sourceId, targetId);
            }
            if (
                relationship == Relationship.VERIFIES
                    && attestation.attestationType != AttestationType.INDEPENDENT_VERIFIER
            ) revert InvalidAttestationRelationship(sourceId, relationship);
            if (
                relationship == Relationship.ATTESTS
                    && attestation.attestationType != AttestationType.OPERATOR
            ) revert InvalidAttestationRelationship(sourceId, relationship);
        }
        if (!_validRelationship(sourceType, relationship, targetType)) {
            revert InvalidProvenanceRelationship(sourceType, relationship, targetType);
        }
        edgeId = keccak256(abi.encode(sourceId, relationship, targetId));
        if (provenanceEdges[edgeId]) revert DuplicateProvenance(edgeId);
        provenanceEdges[edgeId] = true;
        emit ProvenanceLinked(edgeId, sourceId, targetId, relationship);
    }

    function entityExists(bytes32 id) external view returns (bool) {
        return entities[id].entityType != EntityType.NONE;
    }

    function _recordForProgram(bytes32 id, EntityType kind, bytes32 programId, bytes32 commitment)
        internal
    {
        if (entities[programId].entityType != EntityType.PROGRAM) revert UnknownProgram(programId);
        _record(id, kind, commitment, programId);
    }

    function _record(bytes32 id, EntityType kind, bytes32 commitment, bytes32 programId) internal {
        if (id == bytes32(0) || commitment == bytes32(0)) revert InvalidIdentifier();
        if (entities[id].entityType != EntityType.NONE) revert DuplicateEntity(id);
        entities[id] = Entity(kind, commitment, programId, msg.sender, uint64(block.timestamp));
    }

    function _validRelationship(EntityType source, Relationship rel, EntityType target)
        internal
        pure
        returns (bool)
    {
        if (rel == Relationship.FUNDS) {
            return source == EntityType.FUNDING && target == EntityType.ALLOCATION;
        }
        if (rel == Relationship.ALLOCATES_TO) {
            return source == EntityType.ALLOCATION && target == EntityType.DELIVERY;
        }
        if (rel == Relationship.PAYS) {
            return source == EntityType.ALLOCATION && target == EntityType.FINANCIAL_TRANSACTION;
        }
        if (rel == Relationship.EVIDENCES) {
            return source == EntityType.EVIDENCE
                && (target == EntityType.DELIVERY || target == EntityType.OUTCOME);
        }
        if (rel == Relationship.PRODUCES) {
            return source == EntityType.DELIVERY && target == EntityType.OUTCOME;
        }
        if (rel == Relationship.ATTESTS) {
            return source == EntityType.ATTESTATION && target != EntityType.NONE;
        }
        if (rel == Relationship.VERIFIES) {
            return source == EntityType.ATTESTATION && target == EntityType.CLAIM;
        }
        if (rel == Relationship.SUPERSEDES) return source == target && source != EntityType.NONE;
        if (rel == Relationship.SUPPORTS) {
            return (source == EntityType.FINANCIAL_TRANSACTION && target == EntityType.DELIVERY)
                || (source == EntityType.EVIDENCE && target == EntityType.CLAIM)
                || (source == EntityType.OUTCOME && target == EntityType.CLAIM);
        }
        if (rel == Relationship.DELIVERS) {
            return source == EntityType.DELIVERY && target == EntityType.OUTCOME;
        }
        return false;
    }
}
