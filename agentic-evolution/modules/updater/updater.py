"""Base Updater interface."""


class Updater:
    """Base class for updaters.
    
    Takes a proposal and returns a modified agent.
    Update must be functional: never mutate the original agent.
    This allows rollback, versioning, branching evolution, comparison of strategies.
    """
    
    def update(self, agent, proposalObject):
        """
        Returns a NEW agent instance with updated state/prompt/parameters.
        """
        raise NotImplementedError

