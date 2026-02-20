import type { AppWorldObservation, TrajectoryMessage } from "@/types/observation";

function extractTaskLine(content: string): string | null {
  const marker = "Task:";
  const markerIndex = content.indexOf(marker);
  if (markerIndex === -1) {
    return null;
  }

  const afterMarker = content.slice(markerIndex + marker.length);
  const firstNonEmptyLine = afterMarker
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0);

  return firstNonEmptyLine ?? null;
}

function getFirstUserMessage(
  trajectory: TrajectoryMessage[] | undefined
): TrajectoryMessage | undefined {
  return trajectory?.find((step) => step.role === "user");
}

export function getObservationTaskTitle(
  observation: Pick<AppWorldObservation, "task_id" | "trajectory">,
  maxLength = 80
): string {
  const firstUserMessage = getFirstUserMessage(observation.trajectory);
  const rawTitle =
    (firstUserMessage?.content && extractTaskLine(firstUserMessage.content)) ??
    observation.task_id;

  if (rawTitle.length <= maxLength) {
    return rawTitle;
  }

  return `${rawTitle.slice(0, maxLength - 1)}...`;
}
