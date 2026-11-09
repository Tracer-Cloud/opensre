"""The gateway's half of hosting the agent.

A turn belongs to the harness; what lives here is what the gateway supplies so
one can run — an agent per session, turn output to stream into, a console that
cooperates with cancellation, the capability policy, the capacity gate and the
status copy the user sees meanwhile.
"""
