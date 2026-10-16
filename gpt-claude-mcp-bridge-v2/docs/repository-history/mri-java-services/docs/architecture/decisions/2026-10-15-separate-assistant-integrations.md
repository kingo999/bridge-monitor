# Separate local assistant integrations

- Status: Accepted
- Date: 2026-10-15
- Decision authority: Kian, approved 2026-10-15 in the Desktop conversation
- Revision examined: bff38689b8f52711dc635bb21c8e7d170dae1564

## Context

Source observation: shared architecture guidance assigned the architecture role to
one provider and named its integration tools.

## Decision

Kian requested separation of provider-specific code, documents and skills from the
shared security framework, Claude entry points and architecture documents. Kian
explicitly selected removal of all provider mentions and integrations from those
locations, and external ownership of bridge operation tools.

This approves the narrow operating-agreement amendment assigning the architecture
role to the human-selected architecture agent, and relocation of tool-specific
handoff details out of the shared context workflow. Role duties, consultation triggers,
provenance requirements and approval boundaries remain unchanged. Existing accepted
records are preserved.

## Consequences

Architectural assessment: shared guidance no longer assumes a particular
architecture provider or its installed tools. Local integrations retain their own
sources, documents and verification outside the shared framework.

## Evidence

Source observation: this record captures the scope explicitly selected by Kian in
this conversation. It grants no stage, commit, push, release or deployment authority.
