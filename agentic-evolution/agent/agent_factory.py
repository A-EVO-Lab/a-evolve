"""AgentFactory for creating agents."""

from .agent import Agent


class AgentFactory:
    """Creates agents. Nothing more.
    
    No coupling to training or evolution logic.
    """
    
    def create(self, model_name, api_key, system_prompt) -> Agent:
        """Create a new agent instance."""
        return Agent(
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt
        )

