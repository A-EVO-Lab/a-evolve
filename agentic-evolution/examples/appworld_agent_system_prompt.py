"""
AppWorld Agent System Prompt.

System prompt for API-based task execution in AppWorld benchmark.
Based on ReMe/official AppWorld prompt template with injection points for tools, skills, and knowledge.
"""

# Official AppWorld prompt template (based on ReMe format for best performance)
APPWORLD_SYSTEM_PROMPT_OLD = """
USER:
I am your supervisor and you are a super intelligent AI Assistant whose job is to achieve my day-to-day tasks completely autonomously.

To do this, you will need to interact with app/s (e.g., spotify, venmo, etc) using their associated APIs on my behalf. For this you will undertake a *multi-step conversation* using a python REPL environment. That is, you will write the python code and the environment will execute it and show you the result, based on which, you will write python code for the next step and so on, until you've achieved the goal.

Here are three key APIs that you need to know to get more information:

# To get a list of apps that are available to you.
print(apis.api_docs.show_app_descriptions())

# To get the list of apis under any app listed above, e.g. supervisor
print(apis.api_docs.show_api_descriptions(app_name='supervisor'))

# To get the specification of a particular api, e.g. supervisor app's show_account_passwords
print(apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords'))

Each code execution will produce an output that you can use in subsequent calls.

**Key instructions and disclaimers**:

# 1. Only generate valid code blocks, i.e., do not put them in ```...``` or add any extra formatting. Any thoughts should be put as code comments.
# 2. **⚠️ CRITICAL: Do NOT use XML tool-use markup!** Never output <function_calls>, <invoke>, <parameter>, or similar XML tags. Output raw Python code directly, not wrapped in any XML structure. The environment does NOT support XML tool calling.
# 3. You can use the variables from the previous code blocks in the subsequent code blocks.
# 3. Write small chunks of code and only one chunk of code in every step. Make sure everything is working correctly before making any irreversible change.
# 4. The provided Python environment has access to its standard library. Modules and functions that affect the OS, file system or process are disabled.
# 5. Any reference to a file system means the file system *app*, operable via APIs, not the actual file system.
# 6. To interact with apps, only use the provided APIs, not Python packages (e.g., do NOT use `spotipy` for Spotify).
# 7. The API documentation has both input arguments and output JSON schemas. All calls must follow this documentation.
# 8. For APIs that return results in "pages", make sure to consider all pages.
# 9. To obtain current date or time, use Python functions like `datetime.now()`. Do not rely on your existing knowledge.
# 10. For temporal requests, use proper time boundaries (e.g., yesterday = 00:00:00 to 23:59:59).
# 11. Any reference to friends, family or other people refers to the people in my phone's contacts list.
# 12. All personal information and app account credentials are in the "supervisor" app.
# 13. Once completed, call `apis.supervisor.complete_task()`. If answer needed: `apis.supervisor.complete_task(answer=<answer>)`.
# 14. Answers should be entities or numbers, not full sentences (e.g., `answer=10` not `answer="There are ten songs"`). Numbers should be in digits, not words.
# 15. Pass `status="fail"` in complete_task if you cannot solve it and want to exit.
# 16. Make all decisions completely autonomously, no clarifications or confirmations needed.
"""

APPWORLD_SYSTEM_PROMPT = """
USER:
I am your supervisor and you are a super intelligent AI Assistant whose job is to achieve my day-to-day tasks completely autonomously.

To do this, you will need to interact with app/s (e.g., spotify, venmo, etc) using their associated APIs on my behalf. For this you will undertake a *multi-step conversation* using a python REPL environment. That is, you will write the python code and the environment will execute it and show you the result, based on which, you will write python code for the next step and so on, until you've achieved the goal. This environment will let you interact with app/s using their associated APIs on my behalf.

Here are three key APIs that you need to know to get more information

# To get a list of apps that are available to you.
print(apis.api_docs.show_app_descriptions())

# To get the list of apis under any app, e.g. supervisor
print(apis.api_docs.show_api_descriptions(app_name='supervisor'))

# To get the specification of a particular api, e.g. supervisor app's show_account_passwords
print(apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords'))

## Key Instructions

1. Only generate valid Python code blocks. Any thoughts should be put as code comments (# comment).
2. You can use the variables from previous code blocks in subsequent code blocks.
3. Write small chunks of code and only one chunk of code in every step. Make sure everything is working correctly before making any irreversible change.
4. The provided Python environment has access to its standard library. But modules and functions that have a risk of affecting the underlying OS, file system or process are disabled.
5. To interact with apps, only use the provided APIs, and not the corresponding Python packages. E.g., do NOT use `spotipy` for Spotify, do NOT import `apis` - it's already available.
6. The provided API documentation has both the input arguments and the output JSON schemas. All calls to APIs and parsing its outputs must be as per this documentation.
7. For APIs that return results in "pages", make sure to consider all pages.
8. To obtain current date or time, use Python functions like `datetime.now()`.
9. All personal information and app account credentials are stored in the "supervisor" app.
10. Once you have completed the task, call `apis.supervisor.complete_task()`. If the task asks for some information, return it as the answer argument: `apis.supervisor.complete_task(answer=<answer>)`.
11. Answers should be just entity or number, not full sentences, e.g., `answer=10` for "How many songs?".
12. You can pass `status="fail"` in complete_task if you cannot solve the task.
13. Make all decisions autonomously without asking for clarifications.

## Code Format

Generate only valid Python code. Put thoughts as comments:

```python
# First, let me check what apps are available
print(apis.api_docs.show_app_descriptions())
```
"""

# Task instruction template (ReMe format - includes supervisor profile)
# TASK_INSTRUCTION_TEMPLATE = """Using these APIs, now generate code to solve the actual task:

# My name is: {first_name} {last_name}. My personal email is {email} and phone number is {phone_number}.

# Task:

# {instruction}"""

TASK_INSTRUCTION_TEMPLATE = """
Using these APIs, now generate code to solve the actual task:

My name is: {{ supervisor.first_name }} {{ supervisor.last_name }}. My personal email is {{ supervisor.email }} and phone number is {{ supervisor.phone_number }}.

Task:

{{ instruction }}
"""

def build_user_task_prompt(supervisor: dict, instruction: str) -> str:
    return TASK_INSTRUCTION_TEMPLATE.replace(
        "{{ supervisor.first_name }}", supervisor.get('first_name', '')
    ).replace(
        "{{ supervisor.last_name }}", supervisor.get('last_name', '')
    ).replace(
        "{{ supervisor.email }}", supervisor.get('email', '')
    ).replace(
        "{{ supervisor.phone_number }}", supervisor.get('phone_number', '')
    ).replace(
        "{{ instruction }}", instruction
    )


def build_system_prompt(tools, skills, knowledge):
    return APPWORLD_SYSTEM_PROMPT

def build_full_prompt(
    supervisor: dict,
    instruction: str,
    tools: dict = None,
    skills: dict = None,
    knowledge: list = None
) -> str:
    """
    Build complete prompt using ReMe format with supervisor profile and optional enhancements.
    
    This is the main prompt builder for both evolution training and evaluation.
    Uses the proven ReMe format with examples and fills in supervisor info.
    
    Args:
        supervisor: Dict with first_name, last_name, email, phone_number
        instruction: Task instruction
        tools: Optional dict of evolved tools
        skills: Optional dict of learned skills  
        knowledge: Optional list of knowledge entries
        
    Returns:
        Complete prompt string ready for agent
    """
    # Base prompt with example (ReMe format) - fill supervisor placeholders
    base_prompt = APPWORLD_SYSTEM_PROMPT.replace(
        "{{ supervisor.first_name }}", supervisor.get('first_name', '')
    ).replace(
        "{{ supervisor.last_name }}", supervisor.get('last_name', '')
    ).replace(
        "{{ supervisor.email }}", supervisor.get('email', '')
    ).replace(
        "{{ supervisor.phone_number }}", supervisor.get('phone_number', '')
    ).replace(
        "{{ instruction }}", instruction
    )
    
    # Build enhancement sections
    enhancements = []
    
    # Add tools section
    if tools:
        tools_section = "\n## Evolved Tools\n\nYou have specialized tools to help with this task:\n\n"
        for name, info in tools.items():
            desc = info.get('description', 'No description')
            usage = info.get('usage', f'{name}(...)')
            tools_section += f"### {name}\n"
            tools_section += f"**Purpose:** {desc}\n"
            tools_section += f"**Usage:** `{usage}`\n\n"
        enhancements.append(tools_section)
    
    # Add skills section (as experiences like ReMe)
    if skills:
        skills_section = "\n## Learned Experiences\n\nThe following patterns have been learned from successful task completions:\n\n"
        for name, skill in skills.items():
            desc = skill.get('description', skill.get('when_to_use', ''))
            content = skill.get('content', skill.get('code_example', ''))
            skills_section += f"### {name}\n"
            skills_section += f"**When to use:** {desc}\n"
            if content:
                skills_section += f"**Pattern:**\n{content}\n\n"
        enhancements.append(skills_section)
    
    # Add knowledge section
    if knowledge:
        knowledge_section = "\n## Domain Knowledge\n\n"
        for idx, k in enumerate(knowledge, 1):
            topic = k.get('topic', k.get('when_to_use', f'Knowledge {idx}'))
            content = k.get('content', '')
            knowledge_section += f"### {topic}\n{content}\n\n"
        enhancements.append(knowledge_section)
    
    # Insert enhancements before the final task section
    if enhancements:
        # Find where to insert (before the final separator line)
        separator = "----------------------------------------------"
        if separator in base_prompt:
            parts = base_prompt.split(separator)
            # Insert enhancements before the disclaimer section
            enhanced_prompt = parts[0] + separator + "\n\n" + "\n".join(enhancements) + "\n" + separator.join(parts[1:])
            return enhanced_prompt
        else:
            # Fallback: append at end
            return base_prompt + "\n\n" + "\n".join(enhancements)
    
    return base_prompt


def build_system_prompt_only(
    tools: dict = None,
    skills: dict = None,
    knowledge: list = None
) -> str:
    """
    Build just the system prompt part (without task-specific info).
    
    For use cases where system prompt and user message are separate.
    Returns the base system prompt with optional tools/skills/knowledge.
    
    Args:
        tools: Optional dict of evolved tools
        skills: Optional dict of learned skills  
        knowledge: Optional list of knowledge entries
        
    Returns:
        System prompt string
    """
    # Use the base APPWORLD_SYSTEM_PROMPT but stop before the task-specific part
    base = APPWORLD_SYSTEM_PROMPT
    
    # Find where task-specific content starts (after disclaimers)
    marker = "Using these APIs, now generate code to solve the actual task:"
    if marker in base:
        # Keep everything before the task instruction
        base = base[:base.index(marker)]
    
    # Add enhancements
    enhancements = []
    
    if tools:
        tools_section = "\n## Evolved Tools\n\n"
        for name, info in tools.items():
            desc = info.get('description', 'No description')
            tools_section += f"- **{name}**: {desc}\n"
        enhancements.append(tools_section)
    
    if skills:
        skills_section = "\n## Learned Experiences\n\n"
        for name, skill in skills.items():
            desc = skill.get('description', skill.get('when_to_use', ''))
            skills_section += f"- **{name}**: {desc}\n"
        enhancements.append(skills_section)
    
    if knowledge:
        knowledge_section = "\n## Domain Knowledge\n\n"
        for k in knowledge:
            topic = k.get('topic', k.get('when_to_use', 'Knowledge'))
            knowledge_section += f"- **{topic}**\n"
        enhancements.append(knowledge_section)
    
    if enhancements:
        return base.rstrip() + "\n" + "\n".join(enhancements)
    
    return base


# Enhanced system prompt with skills and knowledge (for evolved agent)
APPWORLD_SYSTEM_PROMPT_WITH_SKILLS = """I am your supervisor and you are a super intelligent AI Assistant whose job is to achieve my day-to-day tasks completely autonomously.

To do this, you will need to interact with app/s (e.g., spotify, venmo, etc) using their associated APIs on my behalf. For this you will undertake a *multi-step conversation* using a python REPL environment. That is, you will write the python code and the environment will execute it and show you the result, based on which, you will write python code for the next step and so on, until you've achieved the goal.

## Key APIs for Discovery

# To get a list of apps that are available to you.
print(apis.api_docs.show_app_descriptions())

# To get the list of apis under any app listed above, e.g. supervisor
print(apis.api_docs.show_api_descriptions(app_name='supervisor'))

# To get the specification of a particular api, e.g. supervisor app's show_account_passwords
print(apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords'))

{skills_section}

{knowledge_section}

**Key instructions and disclaimers**:

1. Only generate valid code blocks. Any thoughts should be put as code comments.
2. You can use the variables from the previous code blocks in the subsequent code blocks.
3. Write small chunks of code and only one chunk of code in every step.
4. To interact with apps, only use the provided APIs, not Python packages.
5. For APIs that return results in "pages", make sure to consider all pages.
6. To obtain current date or time, use Python functions like `datetime.now()`.
7. All personal information and app account credentials are in the "supervisor" app.
8. Once completed, call `apis.supervisor.complete_task()`. If answer needed: `apis.supervisor.complete_task(answer=<answer>)`.
9. Answers should be entities or numbers, not full sentences.
10. Make all decisions completely autonomously."""


# Enhanced prompt with evolution features (tools, skills, knowledge)
APPWORLD_SYSTEM_PROMPT_WITH_STATE = """I am your supervisor and you are a super intelligent AI Assistant whose job is to achieve my day-to-day tasks completely autonomously.

To do this, you will need to interact with app/s (e.g., spotify, venmo, etc) using their associated APIs on my behalf. For this you will undertake a *multi-step conversation* using a python REPL environment. That is, you will write the python code and the environment will execute it and show you the result, based on which, you will write python code for the next step and so on, until you've achieved the goal.

Here are three key APIs that you need to know to get more information:

# To get a list of apps that are available to you.
print(apis.api_docs.show_app_descriptions())

# To get the list of apis under any app, e.g. supervisor
print(apis.api_docs.show_api_descriptions(app_name='supervisor'))

# To get the specification of a particular api, e.g. supervisor app's show_account_passwords
print(apis.api_docs.show_api_doc(app_name='supervisor', api_name='show_account_passwords'))

---

## 🧠 Learned Skills & Knowledge

**IMPORTANT: Before writing code, check if any learned skills apply to your current task!**

### Available Skills
{skills_section}

### Available Knowledge
{knowledge_section}

### ⚠️ CRITICAL: Retrieve Before Acting

**If a skill matches your current situation, you MUST retrieve it FIRST before writing code.**

To retrieve a skill for guidance:
```
<RETRIEVE>
READ_SKILL: skill_name
</RETRIEVE>
```

**STOP after the <RETRIEVE> block.** Do NOT include code in the same response.
Wait for the system to return the skill instructions, then follow them in your next response.

---

## 🔧 Available Helper Tools
{tools_section}

**To call a helper tool**, use this format:
```
TOOL_CALL: tool_name(param1="value1", param2="value2")
```

**STOP after the TOOL_CALL.** Wait for the result before continuing.

---

**Key instructions and disclaimers**:

1. Only generate valid code blocks. Any thoughts should be put as code comments.
2. **⚠️ CRITICAL: Do NOT use XML tool-use markup!** Never output <function_calls>, <invoke>, <parameter>, or similar XML tags. Output raw Python code directly, not wrapped in any XML structure. The environment does NOT support XML tool calling.
2. You can use the variables from the previous code blocks in the subsequent code blocks.
3. Write small chunks of code and only one chunk of code in every step. Make sure everything is working correctly before making any irreversible change.
4. The provided Python environment has access to its standard library. Modules and functions that affect the OS, file system or process are disabled.
5. Any reference to a file system means the file system *app*, operable via APIs, not the actual file system.
6. To interact with apps, only use the provided APIs, not Python packages (e.g., do NOT use `spotipy` for Spotify).
7. The API documentation has both input arguments and output JSON schemas. All calls must follow this documentation.
8. For APIs that return results in "pages", make sure to consider all pages.
9. To obtain current date or time, use Python functions like `datetime.now()`. Do not rely on your existing knowledge.
10. For temporal requests, use proper time boundaries (e.g., yesterday = 00:00:00 to 23:59:59).
11. Any reference to friends, family or other people refers to the people in my phone's contacts list.
12. All personal information and app account credentials are in the "supervisor" app.
13. Once completed, call `apis.supervisor.complete_task()`. If answer needed: `apis.supervisor.complete_task(answer=<answer>)`.
14. Answers should be entities or numbers, not full sentences (e.g., `answer=10` not `answer="There are ten songs"`).
15. Pass `status="fail"` in complete_task if you cannot solve it and want to exit.
16. Make all decisions completely autonomously, no clarifications or confirmations needed.

## Output Format

**⚠️ ONLY output ONE of these per response:**

**Option 1: Retrieve skill/knowledge** (if you need guidance first)
```
<RETRIEVE>
READ_SKILL: skill_name
</RETRIEVE>
```
Then STOP. Wait for the skill content before proceeding.

**Option 2: Call a helper tool** (if you need information)
```
TOOL_CALL: tool_name(param="value")
```
Then STOP. Wait for the result before proceeding.

**Option 3: Execute code** (only after you have all needed information)
```python
# Your comment explaining the step
your_code_here
```

**Workflow: Retrieve relevant skills → Call tools if needed → Then execute code**
"""


def build_prompt_with_state(tools: dict = None, skills: dict = None, knowledge: list = None) -> str:
    """Build system prompt with injected state.
    
    Shows skills and knowledge with descriptions to guide LLM on when to retrieve them.
    """
    
    # Format tools section
    if tools:
        tools_lines = []
        for name, tool in tools.items():
            desc = tool.get('description', 'No description')
            tools_lines.append(f"- `{name}`: {desc}")
        tools_section = "\n".join(tools_lines) if tools_lines else "None available."
    else:
        tools_section = "None available."
    
    # Format skills with DESCRIPTION (so LLM knows when to use each)
    if skills:
        skills_lines = []
        for skill_id, skill in skills.items():
            name = skill.get('name', skill_id)
            description = skill.get('description', 'No description')
            triggers = skill.get('triggers', [])
            scope = skill.get('scope', 'strategic_skill')
            scope_icon = "🎯" if scope == 'tactical_patch' else "📚"
            triggers_str = ', '.join(triggers[:3]) if triggers else 'general'
            
            # Show name, when to use, and description
            skills_lines.append(f"{scope_icon} **{name}**")
            skills_lines.append(f"   - When: {triggers_str}")
            skills_lines.append(f"   - {description[:150]}...")
            skills_lines.append("")  # Empty line between skills
            
        skills_section = "\n".join(skills_lines) if skills_lines else "No skills learned yet."
    else:
        skills_section = "No skills learned yet."
    
    # Format knowledge with DESCRIPTION
    if knowledge:
        knowledge_lines = []
        for k in knowledge[:10]:  # Limit to top 10
            topic = k.get('topic', k.get('when_to_use', 'General'))
            content = k.get('content', 'No content')
            entry_type = k.get('type', 'info')
            
            # Show topic and brief content
            knowledge_lines.append(f"📖 **{topic}** ({entry_type})")
            knowledge_lines.append(f"   - {content[:100]}...")
            knowledge_lines.append("")
            
        knowledge_section = "\n".join(knowledge_lines) if knowledge_lines else "No knowledge collected yet."
    else:
        knowledge_section = "No knowledge collected yet."
    
    return APPWORLD_SYSTEM_PROMPT_WITH_STATE.format(
        tools_section=tools_section,
        skills_section=skills_section,
        knowledge_section=knowledge_section
    )


# ============== Skill Retrieval Meta-Tools ==============

def list_available_skills(skills: dict) -> str:
    """List all available skills with triggers and brief descriptions."""
    if not skills:
        return "No skills available."
    
    lines = ["## Available Skills\n"]
    for name, skill in skills.items():
        desc = skill.get('description', 'No description')[:80]
        triggers = skill.get('triggers', [])
        scope = skill.get('scope', 'strategic_skill')
        scope_label = "[PATCH]" if scope == 'tactical_patch' else "[SKILL]"
        triggers_str = ', '.join(triggers[:3]) if triggers else 'general use'
        
        lines.append(f"### {scope_label} {name}")
        lines.append(f"- When: {triggers_str}")
        lines.append(f"- {desc}...\n")
    
    lines.append("\n*Use `READ_SKILL: <name>` to get full instructions.*")
    return "\n".join(lines)


def read_skill(skill_name: str, skills: dict) -> str:
    """Read full content of a specific skill."""
    if skill_name not in skills:
        available = list(skills.keys())
        return f"Skill '{skill_name}' not found. Available: {available}"
    
    skill = skills[skill_name]
    name = skill.get('name', skill_name)
    desc = skill.get('description', '')
    instructions = skill.get('instructions', '')
    triggers = skill.get('triggers', [])
    scope = skill.get('scope', 'strategic_skill')
    
    result = f"## Skill: {name}\n\n"
    result += f"**Scope:** {scope}\n"
    result += f"**Triggers:** {', '.join(triggers) if triggers else 'general'}\n"
    result += f"**Description:** {desc}\n\n"
    result += f"### Instructions\n\n{instructions}"
    
    return result


def list_knowledge(knowledge: list) -> str:
    """List all knowledge topics."""
    if not knowledge:
        return "No knowledge entries available."
    
    lines = ["## Available Knowledge\n"]
    for entry in knowledge:
        topic = entry.get('topic', entry.get('when_to_use', 'General'))
        entry_type = entry.get('type', 'info')
        preview = str(entry.get('content', ''))[:60]
        
        lines.append(f"- **{topic}** ({entry_type}): {preview}...")
    
    lines.append("\n*Use `READ_KNOWLEDGE: <topic>` to get full content.*")
    return "\n".join(lines)


def read_knowledge(topic: str, knowledge: list) -> str:
    """Read full content of a specific knowledge entry."""
    for entry in knowledge:
        entry_topic = entry.get('topic', entry.get('when_to_use', ''))
        if topic.lower() in entry_topic.lower():
            return f"## Knowledge: {entry_topic}\n\n{entry.get('content', str(entry))}"
    
    available = [e.get('topic', e.get('when_to_use', 'unknown')) for e in knowledge]
    return f"Knowledge '{topic}' not found. Available topics: {available}"


def parse_retrieve_block(response: str, skills: dict, knowledge: list) -> tuple:
    """
    Parse RETRIEVE blocks from LLM response and execute commands.
    
    Handles both expected format and hallucinated formats:
    - <RETRIEVE>READ_SKILL: name</RETRIEVE>
    - <invoke name="retrieve_skill"><parameter name="skill_name">name</parameter></invoke>
    - <invoke name="read_skill">...</invoke>
    - <invoke name="list_skills">...</invoke>
    
    Returns:
        (results: str, remaining_response: str)
    """
    import re
    
    results = []
    remaining = response
    
    # Pattern 1: Standard RETRIEVE blocks
    retrieve_pattern = r'<RETRIEVE>(.*?)</RETRIEVE>'
    retrieve_matches = re.findall(retrieve_pattern, response, re.DOTALL)
    
    for block in retrieve_matches:
        lines = block.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if line == 'SKILL_LIST':
                results.append(list_available_skills(skills))
            elif line == 'KNOWLEDGE_LIST':
                results.append(list_knowledge(knowledge))
            elif line.startswith('READ_SKILL:'):
                skill_name = line.replace('READ_SKILL:', '').strip()
                results.append(read_skill(skill_name, skills))
            elif line.startswith('READ_KNOWLEDGE:'):
                topic = line.replace('READ_KNOWLEDGE:', '').strip()
                results.append(read_knowledge(topic, knowledge))
    
    # Remove RETRIEVE blocks from response
    remaining = re.sub(retrieve_pattern, '', remaining, flags=re.DOTALL)
    
    # Pattern 2: Hallucinated <invoke name="retrieve_skill"> or <invoke name="read_skill">
    skill_invoke_pattern = r'<invoke name="(?:retrieve_skill|read_skill|Skill)">\s*<parameter name="(?:skill_name|skill|name)">\s*(.*?)\s*</parameter>\s*</invoke>'
    skill_matches = re.findall(skill_invoke_pattern, response, re.DOTALL)
    
    for skill_name in skill_matches:
        skill_name = skill_name.strip()
        if skill_name:
            results.append(read_skill(skill_name, skills))
    
    # Remove these invoke blocks
    remaining = re.sub(r'<function_calls>\s*' + skill_invoke_pattern + r'\s*</function_calls>', '', remaining, flags=re.DOTALL)
    remaining = re.sub(skill_invoke_pattern, '', remaining, flags=re.DOTALL)
    
    # Pattern 3: Hallucinated <invoke name="list_skills">
    list_skills_pattern = r'<invoke name="(?:list_skills|list_available_skills|SKILL_LIST)".*?</invoke>'
    if re.search(list_skills_pattern, response, re.DOTALL):
        results.append(list_available_skills(skills))
        remaining = re.sub(r'<function_calls>\s*' + list_skills_pattern + r'\s*</function_calls>', '', remaining, flags=re.DOTALL)
        remaining = re.sub(list_skills_pattern, '', remaining, flags=re.DOTALL)
    
    # Pattern 4: Hallucinated <invoke name="read_knowledge">
    knowledge_invoke_pattern = r'<invoke name="(?:read_knowledge|retrieve_knowledge)">\s*<parameter name="(?:topic|knowledge_name|name)">\s*(.*?)\s*</parameter>\s*</invoke>'
    knowledge_matches = re.findall(knowledge_invoke_pattern, response, re.DOTALL)
    
    for topic in knowledge_matches:
        topic = topic.strip()
        if topic:
            results.append(read_knowledge(topic, knowledge))
    
    # Remove these invoke blocks
    remaining = re.sub(r'<function_calls>\s*' + knowledge_invoke_pattern + r'\s*</function_calls>', '', remaining, flags=re.DOTALL)
    remaining = re.sub(knowledge_invoke_pattern, '', remaining, flags=re.DOTALL)
    
    # Pattern 5: Hallucinated <invoke name="list_knowledge">
    list_knowledge_pattern = r'<invoke name="(?:list_knowledge|KNOWLEDGE_LIST)".*?</invoke>'
    if re.search(list_knowledge_pattern, response, re.DOTALL):
        results.append(list_knowledge(knowledge))
        remaining = re.sub(r'<function_calls>\s*' + list_knowledge_pattern + r'\s*</function_calls>', '', remaining, flags=re.DOTALL)
        remaining = re.sub(list_knowledge_pattern, '', remaining, flags=re.DOTALL)
    
    # Clean up any remaining empty function_calls tags
    remaining = re.sub(r'<function_calls>\s*</function_calls>', '', remaining, flags=re.DOTALL)
    remaining = remaining.strip()
    
    return "\n\n---\n\n".join(results) if results else "", remaining

