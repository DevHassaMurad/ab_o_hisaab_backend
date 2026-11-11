# Registration

The current flow collects account details, uploads one selected document, performs real local document analysis, and consumes its verification session when creating the user and profile. Three unsuccessful automatic attempts enable manual review without rejecting registration.

See the [document verification and connection guide](document-verification.md) for architecture, endpoints, schema revision 0005, security policy, dependencies, migration instructions and tests.

The MySQL authentication backend (`main:app`) owns registration and verification. The independent PostgreSQL forecasting backend remains unchanged.
