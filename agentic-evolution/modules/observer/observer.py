"""Base Observer interface."""


class Observer:
    """Base class for observers.
    
    Turns (input, output) into a structured observation.
    """
    
    def observe(self, agent, input_text, output_text):
        """
        Returns an observationObject, which may be:
        - simple dict
        - structured JSON
        - pointer to S3
        - embedding-augmented object
        - reward-annotated object
        """
        raise NotImplementedError

