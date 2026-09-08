# Validate before a provider SDK erases evidence

VR-004 and VR-006 passed their independent behavioral RED/GREEN paths, then
their shared wheel was installed outside the checkout. The useful surprise
was not in regional routing: the SDK's CallInstance conversion could turn a
malformed timestamp into null or lose its offset before the public model
validated it. The model then received plausible data with the error erased.

**Trap:** trusting an SDK's normalized object as the original provider fact.

**Heuristic:** when correctness depends on a distinction, find the earliest
representation that still preserves it. Validate raw page records before
the lossy conversion; retain SDK paging and HTTP ownership rather than
reimplementing transport. A non-editable wheel smoke then tests the other
boundary: distribution metadata, not the developer's ambient environment.

**Seed:** Which other provider deserializers convert invalid values into
ordinary missing data before our typed boundary has a chance to reject them?