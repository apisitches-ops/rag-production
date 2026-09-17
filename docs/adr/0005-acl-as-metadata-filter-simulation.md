# ACL-restricted retrieval is a metadata-filter simulation, not real authentication

To demonstrate the retrieval-security pattern (filtering results by permission) without building a user/auth system, ACL enforcement is simulated: each Document carries an ACL Group, and a query declares an Acting Role that the retriever filters against. There is no login, no real user identity, and no enforcement outside the retrieval filter itself.

## Consequences

This must not be mistaken for a real access-control system. If the project is ever extended with real users, this filter is the seam where actual authorization would need to be plugged in.
