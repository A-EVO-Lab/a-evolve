"""Minimal Agent interface for the self-evolving framework."""


class Agent:
    """The minimal interface any agent must implement.
    
    The agent does not know anything about observation / training.
    state is generic: can include memory, parameters, LoRA weights, etc.
    """
    
    def __init__(self, model_name, api_key, system_prompt):
        self.model_name = model_name
        self.api_key = api_key
        self.original_system_prompt = system_prompt  # The base prompt without patches
        self.system_prompt = system_prompt  # The current prompt (may include patches)
        self.state = {}  # arbitrary mutable state
    
    def call(self, input_text) -> str:
        """Return the model output given input text."""
        raise NotImplementedError
    
    def get_state(self):
        """Get the current state of the agent."""
        return self.state
    
    def set_state(self, new_state):
        """Set the state of the agent."""
        self.state = new_state

