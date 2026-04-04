#!/usr/bin/env python3
"""Replay an ARC-AGI-3 game with the agent and visualize actions in the browser.

Runs the agent on a game, captures every frame, then serves an interactive
visualization on the specified port.

Usage:
    python visualize_replay.py --game sb26 --port 7889 --max-actions 50
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("viz")

WORKSPACE = str(Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "arc")

# Color palette for rendering
PALETTE_HEX = [
    "#FFFFFF", "#CCCCCC", "#999999", "#666666", "#333333", "#000000",
    "#E53AA3", "#FF7BCC", "#F93C31", "#1E93FF", "#88D8F1", "#FFDC00",
    "#FF851B", "#921231", "#4FCC30", "#A356D6",
]
COLOR_NAMES = [
    "white", "off-white", "light gray", "gray", "off-black", "black",
    "magenta", "light magenta", "red", "blue", "light blue", "yellow",
    "orange", "maroon", "green", "purple",
]


def capture_replay(game_id: str, max_actions: int, model_id: str, region: str):
    """Run the agent and capture every frame + action."""
    from agent_evolve.agents.arc.agent import ArcAgent
    from agent_evolve.agents.arc.game_loop import run_game, convert_frame_data
    from agent_evolve.types import Task
    import arc_agi
    from arcengine import GameAction, GameState

    arcade = arc_agi.Arcade()
    env = arcade.make(game_id, render_mode=None)

    agent = ArcAgent(
        workspace_dir=WORKSPACE,
        model_id=model_id,
        region=region,
        max_tokens=2048,
        max_actions=max_actions,
    )
    agent._message_history = []
    agent._total_input_tokens = 0
    agent._total_output_tokens = 0

    system_prompt = agent._build_system_prompt()

    # Capture frames and actions
    captured_frames = []
    captured_actions = []

    def choose_action(frames, latest, meta):
        state = meta.get("state", "")
        if "NOT_PLAYED" in state or "GAME_OVER" in state:
            return GameAction.RESET

        observation = agent._format_observation(frames, latest, meta)
        action_str, reasoning = agent._call_llm(system_prompt, observation, meta)

        # Capture the full reasoning (no truncation)
        captured_actions.append({
            "step": len(captured_actions) + 1,
            "action": action_str,
            "reasoning": reasoning,
        })

        return agent._parse_action(action_str, meta)

    def is_done(frames, latest, meta):
        state = meta.get("state", "")
        if "WIN" in state:
            return True
        wl = meta.get("win_levels", 0)
        lc = meta.get("levels_completed", 0)
        return wl > 0 and lc >= wl

    def on_action(record, frame):
        # Save grid as list of lists
        captured_frames.append({
            "step": record.step,
            "action": record.action,
            "grid": [list(row) for row in frame.grid],
            "levels_completed": record.levels_completed,
            "state": record.state,
            "level_changed": record.level_changed,
        })

    # Get initial frame
    raw = env.reset()
    from agent_evolve.agents.arc.game_loop import convert_frame_data as cvt
    init_frame, init_meta = cvt(raw)
    captured_frames.append({
        "step": 0,
        "action": "INIT",
        "grid": [list(row) for row in init_frame.grid],
        "levels_completed": init_meta.get("levels_completed", 0),
        "state": init_meta.get("state", ""),
        "level_changed": False,
    })

    # Re-create env for the game loop (it needs a fresh reset)
    env2 = arcade.make(game_id, render_mode=None)

    result = run_game(
        env=env2,
        game_id=game_id,
        choose_action=choose_action,
        is_done=is_done,
        max_actions=max_actions,
        on_action=on_action,
    )

    logger.info("Captured %d frames, %d actions", len(captured_frames), len(captured_actions))
    logger.info("Result: levels=%d/%d, actions=%d", result.levels_completed, result.total_levels, result.total_actions)

    return captured_frames, captured_actions, result


def serve_visualization(frames, actions, result, port):
    """Serve the visualization on the given port."""
    from flask import Flask, Response

    app = Flask(__name__)

    frames_json = json.dumps(frames)
    actions_json = json.dumps(actions)
    result_json = json.dumps({
        "game_id": result.game_id,
        "levels_completed": result.levels_completed,
        "total_levels": result.total_levels,
        "total_actions": result.total_actions,
        "per_level_actions": result.per_level_actions,
        "elapsed_sec": round(result.elapsed_sec, 1),
        "game_completed": result.game_completed,
    })
    palette_json = json.dumps(PALETTE_HEX)
    names_json = json.dumps(COLOR_NAMES)

    html = f"""<!DOCTYPE html>
<html>
<head>
<title>ARC-AGI-3 Replay: {result.game_id}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Courier New', monospace; background: #1a1a2e; color: #eee; padding: 20px; }}
  h1 {{ color: #4FCC30; margin-bottom: 10px; font-size: 24px; }}
  .info {{ color: #88D8F1; margin-bottom: 15px; font-size: 14px; }}
  .container {{ display: flex; gap: 20px; }}
  .grid-panel {{ flex: 0 0 auto; }}
  .side-panel {{ flex: 1; min-width: 300px; max-width: 500px; }}
  canvas {{ border: 2px solid #333; display: block; }}
  .controls {{ margin: 15px 0; display: flex; gap: 10px; align-items: center; }}
  button {{ background: #333; color: #eee; border: 1px solid #555; padding: 8px 16px;
            cursor: pointer; font-family: inherit; font-size: 14px; border-radius: 4px; }}
  button:hover {{ background: #444; }}
  button.active {{ background: #4FCC30; color: #000; }}
  .slider-group {{ flex: 1; display: flex; align-items: center; gap: 10px; }}
  input[type=range] {{ flex: 1; accent-color: #4FCC30; }}
  .step-label {{ font-size: 18px; font-weight: bold; min-width: 100px; }}
  .action-info {{ background: #16213e; padding: 12px; border-radius: 6px; margin-bottom: 10px;
                  border-left: 3px solid #4FCC30; font-size: 13px; }}
  .action-name {{ color: #FFDC00; font-size: 16px; font-weight: bold; }}
  .reasoning {{ color: #aaa; margin-top: 6px; white-space: pre-wrap; word-wrap: break-word;
                max-height: 200px; overflow-y: auto; font-size: 12px; }}
  .diff-info {{ background: #1a1a3e; padding: 10px; border-radius: 6px; margin-bottom: 10px;
                font-size: 12px; border-left: 3px solid #F93C31; }}
  .action-list {{ background: #0f0f23; padding: 10px; border-radius: 6px; margin-top: 10px;
                  max-height: 400px; overflow-y: auto; font-size: 12px; }}
  .action-list-item {{ padding: 4px 8px; cursor: pointer; border-radius: 3px; margin-bottom: 2px;
                       display: flex; gap: 8px; align-items: center; }}
  .action-list-item:hover {{ background: #1a1a3e; }}
  .action-list-item.current {{ background: #16213e; border-left: 3px solid #4FCC30; }}
  .action-list-item .step-num {{ color: #555; min-width: 30px; }}
  .action-list-item .action-badge {{ padding: 1px 6px; border-radius: 3px; font-size: 11px;
                                     font-weight: bold; min-width: 70px; text-align: center; }}
  .action-list-item .action-excerpt {{ color: #888; flex: 1; overflow: hidden;
                                       text-overflow: ellipsis; white-space: nowrap; }}
  .legend {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 10px; }}
  .legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 11px; }}
  .legend-swatch {{ width: 14px; height: 14px; border: 1px solid #555; }}
  .timeline {{ display: flex; flex-wrap: wrap; gap: 2px; margin-top: 10px; }}
  .timeline-dot {{ width: 8px; height: 8px; border-radius: 50%; cursor: pointer; }}
  .timeline-dot.current {{ outline: 2px solid #fff; }}
  .timeline-dot.level-change {{ outline: 2px solid #4FCC30; }}
</style>
</head>
<body>
<h1>ARC-AGI-3 Replay: {result.game_id}</h1>
<div class="info">
  Levels: {result.levels_completed}/{result.total_levels} |
  Actions: {result.total_actions} |
  Time: {round(result.elapsed_sec, 1)}s |
  Result: {"COMPLETE" if result.game_completed else "INCOMPLETE"}
</div>

<div class="container">
  <div class="grid-panel">
    <canvas id="grid" width="512" height="512"></canvas>
    <div class="controls">
      <button id="prevBtn" onclick="step(-1)">&lt; Prev</button>
      <button id="playBtn" onclick="togglePlay()">Play</button>
      <button id="nextBtn" onclick="step(1)">Next &gt;</button>
      <div class="slider-group">
        <input type="range" id="slider" min="0" max="0" value="0" oninput="goTo(this.value)">
        <span class="step-label" id="stepLabel">0/0</span>
      </div>
    </div>
    <div class="timeline" id="timeline"></div>
    <div class="legend" id="legend"></div>
  </div>

  <div class="side-panel">
    <div class="action-info" id="actionInfo">
      <div class="action-name" id="actionName">INIT</div>
      <div class="reasoning" id="reasoning">Initial state</div>
    </div>
    <div class="diff-info" id="diffInfo">No changes yet</div>
    <h3 style="color:#88D8F1; margin-top:15px; font-size:14px;">All Actions</h3>
    <div class="action-list" id="actionList"></div>
  </div>
</div>

<script>
const frames = {frames_json};
const actions = {actions_json};
const palette = {palette_json};
const colorNames = {names_json};

const canvas = document.getElementById('grid');
const ctx = canvas.getContext('2d');
const slider = document.getElementById('slider');
const stepLabel = document.getElementById('stepLabel');
const actionName = document.getElementById('actionName');
const reasoning = document.getElementById('reasoning');
const diffInfo = document.getElementById('diffInfo');

let currentStep = 0;
let playing = false;
let playInterval = null;

slider.max = frames.length - 1;

// Action color map
const actionColors = {{
  'INIT': '#888', 'RESET': '#F93C31', 'ACTION1': '#1E93FF',
  'ACTION2': '#4FCC30', 'ACTION3': '#FFDC00', 'ACTION4': '#FF851B',
  'ACTION5': '#A356D6', 'ACTION6': '#E53AA3', 'ACTION7': '#88D8F1',
}};

// Build action list
const actionList = document.getElementById('actionList');
frames.forEach((f, i) => {{
  const item = document.createElement('div');
  item.className = 'action-list-item';
  item.id = `action-item-${{i}}`;

  const stepNum = document.createElement('span');
  stepNum.className = 'step-num';
  stepNum.textContent = `#${{f.step}}`;

  const badge = document.createElement('span');
  badge.className = 'action-badge';
  badge.textContent = f.action;
  badge.style.background = actionColors[f.action] || '#555';
  badge.style.color = (f.action === 'ACTION3' || f.action === 'ACTION5') ? '#000' : '#fff';

  const excerpt = document.createElement('span');
  excerpt.className = 'action-excerpt';
  const act = actions[i - 1];
  // Show first line of reasoning as excerpt
  const fullText = act ? act.reasoning : (i === 0 ? 'Initial state' : '');
  const firstLine = fullText.split('\\n')[0].substring(0, 80);
  excerpt.textContent = firstLine + (firstLine.length >= 80 ? '...' : '');

  if (f.level_changed) {{
    const star = document.createElement('span');
    star.textContent = ' ★';
    star.style.color = '#4FCC30';
    badge.appendChild(star);
  }}

  item.appendChild(stepNum);
  item.appendChild(badge);
  item.appendChild(excerpt);
  item.onclick = () => goTo(i);
  actionList.appendChild(item);
}});

// Build timeline
const timeline = document.getElementById('timeline');
frames.forEach((f, i) => {{
  const dot = document.createElement('div');
  dot.className = 'timeline-dot' + (f.level_changed ? ' level-change' : '');
  dot.style.background = actionColors[f.action] || '#555';
  dot.title = `Step ${{f.step}}: ${{f.action}}`;
  dot.onclick = () => goTo(i);
  timeline.appendChild(dot);
}});

// Build legend
const legend = document.getElementById('legend');
palette.forEach((hex, i) => {{
  const item = document.createElement('div');
  item.className = 'legend-item';
  item.innerHTML = `<div class="legend-swatch" style="background:${{hex}}"></div>${{i}}:${{colorNames[i]}}`;
  legend.appendChild(item);
}});

function drawGrid(grid) {{
  const cellW = canvas.width / grid[0].length;
  const cellH = canvas.height / grid.length;
  for (let y = 0; y < grid.length; y++) {{
    for (let x = 0; x < grid[y].length; x++) {{
      ctx.fillStyle = palette[grid[y][x]] || '#000';
      ctx.fillRect(x * cellW, y * cellH, cellW, cellH);
    }}
  }}
  // Grid lines (subtle)
  if (cellW >= 4) {{
    ctx.strokeStyle = 'rgba(255,255,255,0.05)';
    ctx.lineWidth = 0.5;
    for (let x = 0; x <= grid[0].length; x++) {{
      ctx.beginPath();
      ctx.moveTo(x * cellW, 0);
      ctx.lineTo(x * cellW, canvas.height);
      ctx.stroke();
    }}
    for (let y = 0; y <= grid.length; y++) {{
      ctx.beginPath();
      ctx.moveTo(0, y * cellH);
      ctx.lineTo(canvas.width, y * cellH);
      ctx.stroke();
    }}
  }}
}}

function computeDiff(prev, curr) {{
  if (!prev || !curr) return 'No previous frame';
  let changes = 0;
  let details = [];
  for (let y = 0; y < Math.min(prev.length, curr.length); y++) {{
    for (let x = 0; x < Math.min(prev[y].length, curr[y].length); x++) {{
      if (prev[y][x] !== curr[y][x]) {{
        changes++;
        if (details.length < 10) {{
          details.push(`(${{x}},${{y}}): ${{colorNames[prev[y][x]]}}(${{prev[y][x]}}) -> ${{colorNames[curr[y][x]]}}(${{curr[y][x]}})`);
        }}
      }}
    }}
  }}
  if (changes === 0) return 'No changes';
  let text = `${{changes}} cell(s) changed:\\n${{details.join('\\n')}}`;
  if (changes > 10) text += `\\n... and ${{changes - 10}} more`;
  return text;
}}

function render(idx) {{
  const frame = frames[idx];
  drawGrid(frame.grid);

  stepLabel.textContent = `${{idx}}/${{frames.length - 1}}`;
  slider.value = idx;

  // Action info
  const act = actions[idx - 1];  // actions are offset by 1 (no action for INIT)
  actionName.textContent = frame.action + (frame.level_changed ? ' ★ LEVEL UP!' : '');
  actionName.style.color = actionColors[frame.action] || '#FFDC00';
  reasoning.textContent = act ? act.reasoning : (idx === 0 ? 'Initial state' : '');

  // Diff
  if (idx > 0) {{
    diffInfo.textContent = computeDiff(frames[idx-1].grid, frame.grid);
  }} else {{
    diffInfo.textContent = 'Initial frame';
  }}

  // Timeline highlight
  timeline.querySelectorAll('.timeline-dot').forEach((dot, i) => {{
    dot.classList.toggle('current', i === idx);
  }});

  // Action list highlight + scroll
  actionList.querySelectorAll('.action-list-item').forEach((item, i) => {{
    item.classList.toggle('current', i === idx);
  }});
  const activeItem = document.getElementById(`action-item-${{idx}}`);
  if (activeItem) activeItem.scrollIntoView({{ block: 'nearest' }});
}}

function goTo(idx) {{
  currentStep = parseInt(idx);
  render(currentStep);
}}

function step(delta) {{
  currentStep = Math.max(0, Math.min(frames.length - 1, currentStep + delta));
  render(currentStep);
}}

function togglePlay() {{
  playing = !playing;
  document.getElementById('playBtn').textContent = playing ? 'Pause' : 'Play';
  document.getElementById('playBtn').classList.toggle('active', playing);
  if (playing) {{
    playInterval = setInterval(() => {{
      if (currentStep >= frames.length - 1) {{
        togglePlay();
        return;
      }}
      step(1);
    }}, 200);
  }} else {{
    clearInterval(playInterval);
  }}
}}

// Keyboard controls
document.addEventListener('keydown', (e) => {{
  if (e.key === 'ArrowLeft') step(-1);
  if (e.key === 'ArrowRight') step(1);
  if (e.key === ' ') {{ e.preventDefault(); togglePlay(); }}
}});

// Initial render
render(0);
</script>
</body>
</html>"""

    @app.route("/")
    def index():
        return Response(html, content_type="text/html")

    @app.route("/api/frames")
    def api_frames():
        return Response(frames_json, content_type="application/json")

    logger.info("Serving visualization at http://0.0.0.0:%d", port)
    logger.info("Controls: Arrow keys, Space to play/pause, or use the slider")
    app.run(host="0.0.0.0", port=port, debug=False)


def main():
    parser = argparse.ArgumentParser(description="Visualize ARC-AGI-3 game replay")
    parser.add_argument("--game", default="sb26", help="Game ID prefix (default: sb26)")
    parser.add_argument("--port", type=int, default=7889, help="Port for web server")
    parser.add_argument("--max-actions", type=int, default=50, help="Max actions to replay")
    parser.add_argument("--model", default="us.anthropic.claude-opus-4-6-v1", help="Model ID")
    parser.add_argument("--region", default="us-west-2", help="AWS region")
    args = parser.parse_args()

    # Find full game ID
    import arc_agi
    arcade = arc_agi.Arcade()
    envs = arcade.get_environments()
    game = None
    for e in envs:
        if e.game_id.startswith(args.game):
            game = e
            break
    if not game:
        logger.error("Game '%s' not found. Available: %s", args.game,
                      [e.game_id for e in envs])
        return

    logger.info("Replaying game: %s (%s) with %d max actions",
                game.game_id, game.title, args.max_actions)

    frames, actions, result = capture_replay(
        game.game_id, args.max_actions, args.model, args.region
    )

    serve_visualization(frames, actions, result, args.port)


if __name__ == "__main__":
    main()
