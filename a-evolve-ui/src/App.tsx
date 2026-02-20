import { useEffect, useMemo, useState } from "react";
import { apiClient } from "@/api/client";
import { ArtifactsPanel } from "@/components/artifacts/ArtifactsPanel";
import { BatchDetail } from "@/components/batch/BatchDetail";
import { BatchList } from "@/components/batch/BatchList";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { EvolutionTimeline } from "@/components/dashboard/EvolutionTimeline";
import { EvaluationComparison } from "@/components/comparison/EvaluationComparison";
import { EvolveConfigForm } from "@/components/evolve/EvolveConfigForm";
import { EvolveLeftPanel } from "@/components/evolve/EvolveLeftPanel";
import { AppLayout } from "@/components/layout/AppLayout";
import { TwoPanelLayout } from "@/components/layout/TwoPanelLayout";
import { PipelineViewer } from "@/components/pipeline/PipelineViewer";
import { SessionSummary } from "@/components/session/SessionSummary";
import { TrajectoryViewer } from "@/components/trajectory/TrajectoryViewer";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useBatchData } from "@/hooks/useBatchData";
import { useEvolveSession } from "@/hooks/useEvolveSession";
import { useSessionData } from "@/hooks/useSession";
import type { EvolveConfig } from "@/types/evolve-config";
import type { PipelineData } from "@/types/evolve";
import type { AppWorldObservation } from "@/types/observation";
import type { SessionEvaluationsResponse } from "@/types/session";

export function App() {
  const {
    sessions,
    selectedSessionId,
    selectedSession,
    setSelectedSessionId,
    reloadSessions,
    loading,
    error
  } = useSessionData();
  const [mode, setMode] = useState<"browse" | "evolve">("browse");

  const activeSessionId =
    mode === "evolve" && selectedSessionId
      ? selectedSessionId
      : selectedSessionId;

  const {
    batches,
    selectedBatchId,
    setSelectedBatchId,
    batchObservations,
    reloadBatches,
    reloadSelectedBatch,
    error: batchError
  } = useBatchData(activeSessionId);

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskObservation, setTaskObservation] = useState<AppWorldObservation | null>(
    null
  );
  const [taskEvaluation, setTaskEvaluation] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineData | null>(null);
  const [tools, setTools] = useState<Array<Record<string, unknown>>>([]);
  const [skills, setSkills] = useState<Array<{ name: string; path: string; content: string }>>(
    []
  );
  const [knowledge, setKnowledge] = useState<Array<Record<string, unknown>>>([]);
  const [allTrainingObservations, setAllTrainingObservations] = useState<
    AppWorldObservation[]
  >([]);
  const [evaluations, setEvaluations] = useState<SessionEvaluationsResponse | null>(null);
  const [evaluationsLoading, setEvaluationsLoading] = useState(false);
  const [evaluationsError, setEvaluationsError] = useState<string | null>(null);
  const [mainTab, setMainTab] = useState("dashboard");

  const loadArtifacts = async (sessionId: string) => {
    const [toolsRes, skillsRes, knowledgeRes] = await Promise.all([
      apiClient.getTools(sessionId),
      apiClient.getSkills(sessionId),
      apiClient.getKnowledge(sessionId)
    ]);
    setTools(toolsRes.tools);
    setSkills(skillsRes.skills);
    setKnowledge(knowledgeRes.knowledge);
  };

  const evolve = useEvolveSession({
    onBatchComplete: () => {
      void reloadBatches();
      void reloadSelectedBatch();
      void reloadSessions();
    },
    onArtifactChange: () => {
      if (activeSessionId) {
        void loadArtifacts(activeSessionId).catch(() => undefined);
      }
    },
    onDone: (sessionId, status) => {
      void reloadSessions();
      setSelectedSessionId(sessionId);
      if (status === "completed") {
        setMode("browse");
      }
    }
  });

  useEffect(() => {
    setSelectedTaskId(null);
    setTaskObservation(null);
    setTaskEvaluation(null);
  }, [selectedBatchId]);

  useEffect(() => {
    if (!activeSessionId || !selectedBatchId) {
      setPipeline(null);
      return;
    }
    void apiClient
      .getPipeline(activeSessionId, selectedBatchId)
      .then(setPipeline)
      .catch(() => setPipeline(null));
  }, [activeSessionId, selectedBatchId]);

  useEffect(() => {
    if (!activeSessionId || !selectedTaskId) {
      setTaskObservation(null);
      setTaskEvaluation(null);
      return;
    }
    void apiClient
      .getTask(activeSessionId, selectedTaskId)
      .then((data) => {
        setTaskObservation(data.observation);
        setTaskEvaluation(data.artifacts.evaluation_report);
      })
      .catch(() => {
        setTaskObservation(null);
        setTaskEvaluation(null);
      });
  }, [activeSessionId, selectedTaskId]);

  useEffect(() => {
    if (!activeSessionId) {
      return;
    }
    void loadArtifacts(activeSessionId)
      .catch(() => {
        setTools([]);
        setSkills([]);
        setKnowledge([]);
      });
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId) {
      setEvaluations(null);
      setEvaluationsError(null);
      setEvaluationsLoading(false);
      return;
    }
    let cancelled = false;
    setEvaluationsLoading(true);
    setEvaluationsError(null);
    void apiClient
      .getEvaluations(activeSessionId)
      .then((data) => {
        if (!cancelled) {
          setEvaluations(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setEvaluations(null);
          setEvaluationsError(
            err instanceof Error ? err.message : "Failed to load evaluation data"
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setEvaluationsLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId || batches.length === 0) {
      setAllTrainingObservations([]);
      return;
    }
    let cancelled = false;
    void Promise.all(batches.map((batch) => apiClient.getBatch(activeSessionId, batch.id)))
      .then((responses) => {
        if (cancelled) {
          return;
        }
        const flattened = responses.flatMap((res) => res.observations);
        setAllTrainingObservations(flattened);
      })
      .catch(() => {
        if (!cancelled) {
          setAllTrainingObservations([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId, batches]);

  const selectedObservation = useMemo(
    () => batchObservations.find((obs) => obs.task_id === selectedTaskId) ?? null,
    [batchObservations, selectedTaskId]
  );

  const onStartEvolve = async (config: EvolveConfig) => {
    try {
      const sessionId = await evolve.startEvolve(config);
      await reloadSessions();
      setSelectedSessionId(sessionId);
    } catch {
      // error is already captured in evolve.state.error by the hook
    }
  };

  const onSelectBatch = (batchId: string) => {
    setSelectedBatchId(batchId);
    setMainTab("batch");
  };

  const onSelectTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setMainTab("trajectory");
  };

  const rightPanel = (
    <div className="h-full overflow-auto p-3">
      {mode === "evolve" && evolve.state.status === "idle" ? (
        <div className="rounded-lg border border-slate-700 bg-slate-900/70 p-4 text-sm text-slate-300">
          Configure environment/model in the top section and click Start Evolve to
          launch a new session.
        </div>
      ) : (
        <Tabs value={mainTab} onValueChange={setMainTab}>
          <TabsList className="mb-3 flex flex-wrap gap-2">
            {["dashboard", "batch", "trajectory", "pipeline", "comparison", "artifacts"].map(
              (tab) => (
                <TabsTrigger
                  key={tab}
                  value={tab}
                  className="rounded-md border border-slate-700 px-2 py-1 text-xs capitalize transition-colors duration-150 data-[state=active]:border-indigo-500 data-[state=active]:bg-indigo-500/20 data-[state=active]:text-indigo-100 hover:bg-slate-800"
                >
                  {tab}
                </TabsTrigger>
              )
            )}
          </TabsList>
          <TabsContent value="dashboard">
            <Dashboard
              session={selectedSession}
              batches={batches}
            />
            <div className="mt-3">
              <EvolutionTimeline batches={batches} />
            </div>
          </TabsContent>
          <TabsContent value="batch">
            <BatchDetail
              observations={batchObservations}
              selectedTaskId={selectedTaskId}
              onSelectTask={onSelectTask}
            />
          </TabsContent>
          <TabsContent value="trajectory">
            <TrajectoryViewer
              observation={taskObservation ?? selectedObservation}
              evaluationReport={taskEvaluation}
            />
          </TabsContent>
          <TabsContent value="pipeline">
            <PipelineViewer pipeline={pipeline} observations={batchObservations} activeStage={evolve.state.activeStage} />
          </TabsContent>
          <TabsContent value="comparison">
            <EvaluationComparison
              trainingObservations={allTrainingObservations}
              evaluations={evaluations}
              loading={evaluationsLoading}
              error={evaluationsError}
              onOpenBatchTab={() => setMainTab("batch")}
            />
          </TabsContent>
          <TabsContent value="artifacts">
            <ArtifactsPanel
              tools={tools}
              skills={skills}
              knowledge={knowledge}
            />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );

  return (
    <AppLayout
      sessions={sessions}
      selectedSessionId={selectedSessionId}
      onSelectSession={setSelectedSessionId}
      mode={mode}
      onModeChange={setMode}
      evolveHeaderContent={
        <EvolveConfigForm
          onSubmit={onStartEvolve}
          loading={evolve.state.status === "configuring"}
          error={evolve.state.error}
        />
      }
    >
      {loading ? (
        <div className="p-4 text-sm text-slate-300">Loading sessions...</div>
      ) : error ? (
        <div className="p-4 text-sm text-rose-300">{error}</div>
      ) : (
        <TwoPanelLayout
          left={
            mode === "evolve" ? (
              <EvolveLeftPanel
                evolveState={evolve.state}
                batches={batches}
                selectedBatchId={selectedBatchId}
                onSelectBatch={onSelectBatch}
                onStop={() => {
                  void evolve.stopEvolve();
                }}
              />
            ) : (
              <div className="flex h-full flex-col overflow-hidden">
                <div className="flex-shrink-0 border-b border-slate-700 p-3">
                  <SessionSummary session={selectedSession} />
                  {batchError ? (
                    <div className="mt-2 text-xs text-rose-300">{batchError}</div>
                  ) : null}
                </div>
                <div className="min-h-0 flex-1">
                  <BatchList
                    batches={batches}
                    selectedBatchId={selectedBatchId}
                    onSelectBatch={onSelectBatch}
                  />
                </div>
              </div>
            )
          }
          right={rightPanel}
        />
      )}
    </AppLayout>
  );
}
