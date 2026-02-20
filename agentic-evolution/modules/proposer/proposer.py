"""Base Proposer interface."""

class Proposer:
    """Base class for proposers.
    
    Uses observations to propose how to improve the agent.
    """
    
    def propose(self, agent, observationObject, context=None):
        """
        Returns a proposalObject describing what should be changed.
        Proposal may include:
        - new instructions
        - new memory entries
        - synthetic training samples
        - fine-tuning batch
        - model parameters to update
        """
        
        raise NotImplementedError

