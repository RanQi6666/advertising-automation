import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { test } from "node:test";

const appSource = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

function componentSource(name: string, nextName: string): string {
  const start = appSource.indexOf(`function ${name}(`);
  const end = appSource.indexOf(`function ${nextName}(`);
  assert.ok(start > 0, `${name} component should exist`);
  assert.ok(end > start, `${nextName} component should follow ${name}`);
  return appSource.slice(start, end);
}

function asyncFunctionSource(name: string, nextName: string): string {
  const start = appSource.indexOf(`async function ${name}(`);
  const asyncEnd = appSource.indexOf(`async function ${nextName}(`);
  const syncEnd = appSource.indexOf(`function ${nextName}(`);
  const endCandidates = [asyncEnd, syncEnd].filter((index) => index > start);
  const end = Math.min(...endCandidates);
  assert.ok(start > 0, `${name} function should exist`);
  assert.ok(end > start, `${nextName} function should follow ${name}`);
  return appSource.slice(start, end);
}

test("review-facing topic and copy labels are Chinese", () => {
  assert.match(appSource, /<span className="topic-kicker">选题方向<\/span>/);
  assert.doesNotMatch(appSource, /<span className="topic-kicker">Topic Direction<\/span>/);

  assert.match(appSource, /<span>正文<\/span>/);
  assert.match(appSource, /<span>标题<\/span>/);
  assert.match(appSource, /<span>描述<\/span>/);
  assert.doesNotMatch(appSource, /<span>Primary Text<\/span>/);
  assert.doesNotMatch(appSource, /<span>Headline<\/span>/);
  assert.doesNotMatch(appSource, /<span>Description<\/span>/);
});

test("video review is moved into the builder column and duplicated script editing is removed", () => {
  const reviewIndex = appSource.indexOf("video-review-inline-panel");
  const sideStackIndex = appSource.indexOf("video-side-stack");

  assert.ok(reviewIndex > 0, "video review panel should have an inline builder placement class");
  assert.ok(sideStackIndex > 0, "video page should still render the preview side stack");
  assert.ok(reviewIndex < sideStackIndex, "video review panel should appear before the right-side preview stack");
  assert.doesNotMatch(appSource, /className="video-overview-strip"/);
  assert.doesNotMatch(appSource, /className="storyboard-editor-details video-storyboard-editor"/);
});

test("style and camera guidance is edited only in the image script console", () => {
  const creativesViewSource = componentSource("CreativesView", "CreativeSlotCard");
  const videosViewSource = componentSource("VideosView", "DeliveryConfirmDialog");

  assert.match(creativesViewSource, /className="video-instructions script-console-input"/);
  assert.match(creativesViewSource, /placeholder="风格与镜头要求"/);
  assert.doesNotMatch(videosViewSource, /htmlFor="video-instructions"/);
  assert.doesNotMatch(videosViewSource, /id="video-instructions"/);
  assert.doesNotMatch(videosViewSource, /placeholder="视频风格或镜头要求"/);
});

test("video script generation does not depend on selected reference images", () => {
  const generateSource = asyncFunctionSource(
    "handleGenerateVideoStoryboard",
    "handleRewriteVideoStoryboard",
  );
  const rewriteSource = asyncFunctionSource("handleRewriteVideoStoryboard", "handleCreateVideo");
  const createVideoSource = asyncFunctionSource("handleCreateVideo", "selectedCreativeIdsForVideo");

  assert.match(generateSource, /api\.generateVideoStoryboardTask\(\s*selectedCampaign\.id,\s*\[\],/s);
  assert.doesNotMatch(
    generateSource,
    /api\.generateVideoStoryboardTask\(\s*selectedCampaign\.id,\s*sourceIds,/s,
  );
  assert.match(rewriteSource, /creativeAssetIds:\s*\[\]/);
  assert.doesNotMatch(rewriteSource, /creativeAssetIds:\s*sourceIds/);
  assert.match(createVideoSource, /creativeAssetIds:\s*sourceIds/);
});

test("video storyboard generation uses text queue tasks", () => {
  const generateSource = asyncFunctionSource(
    "handleGenerateVideoStoryboard",
    "handleRewriteVideoStoryboard",
  );
  const rewriteSource = asyncFunctionSource("handleRewriteVideoStoryboard", "handleCreateVideo");

  assert.match(apiSource, /generateVideoStoryboardTask:/);
  assert.match(apiSource, /\/videos\/storyboard\/task/);
  assert.match(apiSource, /rewriteVideoStoryboardTask:/);
  assert.match(apiSource, /\/videos\/storyboard\/rewrite\/task/);
  assert.match(appSource, /videoStoryboardFromGenerationTask/);
  assert.match(appSource, /videoStoryboardTextFromGenerationTask/);

  assert.match(generateSource, /api\.generateVideoStoryboardTask\(/);
  assert.match(generateSource, /waitForGenerationTask\(\s*task\.id,\s*"创意脚本"/s);
  assert.match(generateSource, /applyVideoStoryboardGenerationTask\(completedTask\)/);
  assert.doesNotMatch(generateSource, /api\.streamVideoStoryboard\(/);
  assert.doesNotMatch(generateSource, /confirmGenerationAttempt/);

  assert.match(rewriteSource, /api\.rewriteVideoStoryboardTask\(/);
  assert.match(rewriteSource, /waitForGenerationTask\(\s*task\.id,\s*"脚本改写"/s);
  assert.match(rewriteSource, /applyVideoStoryboardGenerationTask\(completedTask\)/);
  assert.doesNotMatch(rewriteSource, /api\.streamRewriteVideoStoryboard\(/);
});

test("video generation uses task polling and restore cache", () => {
  const createVideoSource = asyncFunctionSource("handleCreateVideo", "handleStartVideoGeneration");
  const startVideoSource = asyncFunctionSource("handleStartVideoGeneration", "selectedCreativeIdsForVideo");

  assert.match(apiSource, /startVideoGenerationTask:\s*\(videoId:\s*string\)\s*=>\s*post<GenerationTask>/);
  assert.match(apiSource, /\/videos\/\$\{videoId\}\/generate\/task/);
  assert.match(appSource, /ACTIVE_VIDEO_GENERATION_TASK_CACHE_KEY/);
  assert.match(appSource, /loadActiveVideoGenerationTaskCache/);
  assert.match(appSource, /saveActiveVideoGenerationTaskCache/);
  assert.match(appSource, /clearActiveVideoGenerationTaskCache/);
  assert.match(createVideoSource, /api\.startVideoGenerationTask\(created\.id\)/);
  assert.match(createVideoSource, /waitForGenerationTask\(\s*task\.id,\s*"视频"/s);
  assert.match(startVideoSource, /api\.startVideoGenerationTask\(videoId\)/);
  assert.match(startVideoSource, /applyVideoGenerationTask/);
  assert.doesNotMatch(createVideoSource, /api\.startVideoGeneration\(created\.id\)/);
  assert.doesNotMatch(startVideoSource, /api\.startVideoGeneration\(videoId\)/);
});

test("image-page script console textareas are readable on a light editing surface", () => {
  assert.match(
    stylesSource,
    /\.script-console-textarea,\s*\.script-console-input\s*{[^}]*background:\s*#ffffff;[^}]*color:\s*#102326;/s,
  );
  assert.match(
    stylesSource,
    /\.script-console-textarea::placeholder,\s*\.script-console-input::placeholder\s*{[^}]*color:\s*#64748b;/s,
  );
});

test("global notices live in a collapsible topbar message center", () => {
  assert.match(appSource, /className="message-center"/);
  assert.match(appSource, /className="message-center-popover"/);
  assert.match(appSource, /通知\(\{noticeCount\}\)/);
  assert.match(appSource, /const \[messageHistory, setMessageHistory\]/);
  assert.match(appSource, /messages=\{messageHistory\}/);
  assert.match(appSource, /prependMessageHistory/);
  assert.doesNotMatch(appSource, /消息\(0\)/);
  assert.doesNotMatch(appSource, /待办\(0\)/);
  assert.doesNotMatch(appSource, /className=\{`banner \$\{bannerTone\}`\}/);
});

test("topbar page header keeps only the large view title", () => {
  const titleStart = appSource.indexOf('<div className="topbar-title">');
  const actionsStart = appSource.indexOf('<div className="topbar-actions">', titleStart);
  assert.ok(titleStart > 0, "topbar title should exist");
  assert.ok(actionsStart > titleStart, "topbar actions should follow the title");

  const topbarTitleSource = appSource.slice(titleStart, actionsStart);
  assert.match(topbarTitleSource, /<h1>\{viewTitle\(activeView\)\}<\/h1>/);
  assert.doesNotMatch(topbarTitleSource, /topbar-eyebrow/);
  assert.doesNotMatch(topbarTitleSource, /viewSubtitles/);
  assert.doesNotMatch(appSource, /const viewSubtitles/);
});

test("workflow final preview package is kept clean and opens only from state", () => {
  const workflowSource = componentSource("WorkflowView", "DeliveryConfirmDialog");
  const finalPackageStart = workflowSource.indexOf('className="final-package-panel"');
  const finalPackageEnd = workflowSource.indexOf("</details>", finalPackageStart);
  assert.ok(finalPackageStart > 0, "final package panel should exist");
  assert.ok(finalPackageEnd > finalPackageStart, "final package panel should close");
  const finalPackageSource = workflowSource.slice(finalPackageStart, finalPackageEnd);

  assert.match(workflowSource, /className="final-package-panel"/);
  assert.match(workflowSource, /open=\{finalPackageOpen\}/);
  assert.match(appSource, /setFinalPackageOpen\(true\)/);
  assert.match(workflowSource, /action:\s*"生成预览包"/);
  assert.doesNotMatch(workflowSource, /open=\{summary\.final\.done \|\| Boolean\(finalPayloadDraft\.trim\(\)\)\}/);
  assert.doesNotMatch(workflowSource, /className=\{`brand-safety-card/);
  assert.doesNotMatch(workflowSource, /id="final-notes"/);
  assert.doesNotMatch(workflowSource, />生产结果</);
  assert.doesNotMatch(finalPackageSource, /onClick=\{onPrepareFinal\}/);
  assert.doesNotMatch(finalPackageSource, />生成预审包</);
  assert.doesNotMatch(finalPackageSource, />生成预览包</);
});

test("task context investment parameters hide external order and ad title fields", () => {
  const start = appSource.indexOf("function adGenerationJobFields(");
  const end = appSource.indexOf("function adGenerationRawContent(", start);
  assert.ok(start > 0, "adGenerationJobFields should exist");
  assert.ok(end > start, "adGenerationRawContent should follow adGenerationJobFields");

  const fieldSource = appSource.slice(start, end);
  assert.match(fieldSource, /任务状态:\s*statusLabel\(job\.status\)/);
  assert.match(fieldSource, /投放链接:\s*creativePayload\.link \|\| campaignLandingUrl\(campaign\)/);
  assert.doesNotMatch(fieldSource, /外部工单/);
  assert.doesNotMatch(fieldSource, /标题:\s*creativePayload\.ads_name/);
});

test("job list identifiers are displayed as serial work order numbers instead of short uuids", () => {
  assert.match(appSource, /工单编号 \{adGenerationJobNumber\(job, jobs\)\}/);
  assert.doesNotMatch(appSource, /\{job\.external_order_id \|\| shortId\(job\.id\)\}/);
});
test("topic regeneration feedback box stays blank until the user types", () => {
  const topicsViewSource = componentSource("TopicsView", "CopyView");

  assert.match(topicsViewSource, /className="topic-feedback-input"/);
  assert.doesNotMatch(topicsViewSource, /placeholder=/);
  assert.doesNotMatch(appSource, /减少价格卖点/);
  assert.doesNotMatch(appSource, /印度家庭客厅观影/);
});

test("topic generation progress is isolated per campaign", () => {
  const generateSource = asyncFunctionSource("handleGenerateTopics", "handleRetryTopicSlot");
  const retrySource = asyncFunctionSource("handleRetryTopicSlot", "handleSelectTopic");
  const topicsViewSource = componentSource("TopicsView", "CopyView");

  assert.match(appSource, /type TopicGenerationSlot = \{[^}]*campaignId:\s*string;/s);
  assert.match(appSource, /const selectedTopicGenerationSlots = useMemo\(/);
  assert.match(
    appSource,
    /topicGenerationSlots\.filter\(\(slot\) => slot\.campaignId === selectedCampaign\.id\)/,
  );
  assert.match(appSource, /topicGenerationSlots=\{selectedTopicGenerationSlots\}/);
  assert.match(appSource, /function initialTopicSlots\(limit: number, campaignId: string\)/);
  assert.match(appSource, /campaignId,\s*index: index \+ 1/s);

  assert.match(generateSource, /const campaignId = selectedCampaign\.id;/);
  assert.match(generateSource, /slot\.campaignId === campaignId && slot\.status === "loading"/);
  assert.match(generateSource, /replaceTopicGenerationSlots\(campaignId, TOPIC_GENERATION_LIMIT\)/);
  assert.match(generateSource, /isSelectedCampaign\(campaignId\)/);
  assert.doesNotMatch(generateSource, /setTopicGenerationSlots\(initialTopicSlots/);

  assert.match(retrySource, /const campaignId = selectedCampaign\.id;/);
  assert.match(retrySource, /updateTopicGenerationSlot\(campaignId, slotIndex,/);
  assert.match(retrySource, /isSelectedCampaign\(campaignId\)/);

  assert.doesNotMatch(topicsViewSource, /loading === "topics"/);
});

test("campaign switching ignores stale artifact refresh responses", () => {
  const refreshSource = asyncFunctionSource("refreshCampaignData", "handleSelectSampleWorkOrder");

  assert.match(appSource, /const campaignDataRequestRef = useRef\(0\);/);
  assert.match(appSource, /function clearVisibleCampaignWorkflowState\(\)/);
  assert.match(refreshSource, /const requestId = \+\+campaignDataRequestRef\.current;/);
  assert.match(refreshSource, /clearVisibleCampaignWorkflowState\(\);/);
  assert.match(refreshSource, /requestId !== campaignDataRequestRef\.current/);
});

test("deleted work orders are removed from the visible task monitor immediately", () => {
  const deleteSource = asyncFunctionSource("handleDeleteJob", "buildTopicGenerationSignals");

  assert.match(appSource, /function removeGenerationTasksForDeletedJob\(jobId: string, campaignId: string \| null\)/);
  assert.match(appSource, /function clearDeletedCampaignWorkflowState\(campaignId: string \| null, forceVisible: boolean\)/);
  assert.match(deleteSource, /const deletedCampaignId = job \? adGenerationCampaignId\(job\) : null;/);
  assert.match(deleteSource, /clearDeletedCampaignWorkflowState\(deletedCampaignId, selectedJobId === jobId\);/);
  assert.match(deleteSource, /removeGenerationTasksForDeletedJob\(jobId, deletedCampaignId\);/);
  assert.match(appSource, /slot\.campaignId !== campaignId/);
  assert.match(appSource, /setSelectedCampaignId\(\(current\) => \(!campaignId \|\| current === campaignId \? null : current\)\)/);
  assert.match(appSource, /task\.display_context\.ad_generation_job_id/);
});

test("active task monitor polling is responsive without becoming one-second polling", () => {
  assert.match(appSource, /const TASK_MONITOR_ACTIVE_REFRESH_MS = 3000;/);
  assert.doesNotMatch(appSource, /const TASK_MONITOR_ACTIVE_REFRESH_MS = 1000;/);
});

test("image-only jobs do not mark video or final review complete before prior stages are ready", () => {
  const summarySource = componentSource("buildWorkflowSummary", "stepSummary");

  assert.doesNotMatch(summarySource, /const videoDone = !videoRequired \|\| videos\.length > 0;/);
  assert.match(summarySource, /const videoDone = videoRequired && videos\.length > 0;/);
  assert.match(summarySource, /const finalReady = fieldsDone && topicDone && copyDone && imageDone && \(!videoRequired \|\| videoDone\);/);
  assert.match(summarySource, /final:\s*stepSummary\("final", "最终预审", finalReady, "确认后回传投放系统", finalReady\)/);
});

test("copy rewrite feedback removes preset hints and keeps only the version note", () => {
  const copyViewSource = componentSource("CopyView", "CreativeSlotCard");
  const feedbackStart = copyViewSource.indexOf('className="copy-feedback-card"');
  const actionsStart = copyViewSource.indexOf('className="copy-actions"', feedbackStart);
  assert.ok(feedbackStart > 0, "copy feedback card should exist");
  assert.ok(actionsStart > feedbackStart, "copy actions should follow feedback input");
  const feedbackSource = copyViewSource.slice(feedbackStart, actionsStart);
  const versionNote = "\u4f1a\u57fa\u4e8e\u5f53\u524d\u7248\u672c\u91cd\u5199\uff0c\u5e76\u4fdd\u7559\u65b0\u65e7\u7248\u672c\u4f9b\u6bd4\u8f83";

  assert.ok(feedbackSource.includes(`<span>${versionNote}</span>`));
  assert.doesNotMatch(copyViewSource, /feedbackTags/);
  assert.doesNotMatch(copyViewSource, /appendFeedback/);
  assert.doesNotMatch(feedbackSource, /copy-feedback-tags/);
  assert.doesNotMatch(feedbackSource, /copy-feedback-tag/);
  assert.doesNotMatch(feedbackSource, /placeholder=/);
  assert.doesNotMatch(appSource, /\u4e0d\u6ee1\u610f\uff1f\u5199\u4fee\u6539\u610f\u89c1\u518d\u751f\u6210/);
  assert.doesNotMatch(appSource, /\u4f8b\u5982\uff1a\u6587\u6848\u66f4\u77ed/);
  assert.doesNotMatch(appSource, /\u7a81\u51fa\u4f7f\u7528\u573a\u666f/);
  assert.doesNotMatch(stylesSource, /\.copy-feedback-tags/);
  assert.doesNotMatch(stylesSource, /\.copy-feedback-tag/);
});

test("keyframe scheme cards use clear frame labels and blank rewrite feedback", () => {
  const creativesViewSource = componentSource("CreativesView", "CreativeSlotCard");
  const creativeSlotSource = componentSource("CreativeSlotCard", "CreativeAssetMiniCard");

  assert.match(creativesViewSource, /className="keyframe-variant-summary"/);
  assert.match(creativesViewSource, /keyframe-variant-select-button/);
  assert.doesNotMatch(stylesSource, /\.keyframe-variant-head span\s*\{/);
  assert.match(stylesSource, /\.keyframe-variant-summary\s*\{/);
  assert.match(stylesSource, /\.keyframe-variant-select-button\.primary-button\s*\{/);

  assert.match(creativeSlotSource, /function CreativeSlotCard\(/);
  assert.match(creativeSlotSource, /<span>\{creativeSlotFrameLabel\(asset, slot\.index\)\}<\/span>/);
  assert.doesNotMatch(creativeSlotSource, /<span>候选 \{creativeImageIndex\(asset, slot\.index\)\}<\/span>/);
  assert.match(appSource, /function creativeSlotFrameLabel\(asset: CreativeAsset, fallbackIndex: number\): string/);
  assert.match(appSource, /position === 1 \? "首帧图" : position === 2 \? "尾帧图"/);
  assert.doesNotMatch(appSource, /例如：增强动感和金属质感/);
  assert.doesNotMatch(creativesViewSource, /placeholder="例如：增强动感和金属质感/);
});

test("keyframe image generation launches scheme tasks and stores multiple task ids", () => {
  assert.match(appSource, /generateKeyframeCreativesTasks/);
  assert.match(apiSource, /\/creatives\/generate\/keyframe-tasks/);
  assert.match(apiSource, /generation_mode: "video_keyframe_variants"/);
  assert.match(appSource, /normalizeActiveImageGenerationTaskCache/);
  assert.match(appSource, /targetIndicesMax/);
  assert.match(appSource, /taskIds: tasks\.map\(\(task\) => task\.id\)/);
});

test("active image tasks stay pending and continue polling after foreground timeout", () => {
  const generateSource = asyncFunctionSource("handleGenerateCreativesTask", "handleGenerateCreativesStream");
  const retryTaskSource = asyncFunctionSource("handleRetryCreativeSlotTask", "handleRetryCreativeSlot");
  const retryGroupSource = asyncFunctionSource("handleRetryKeyframeGroupTask", "handleRegenerateKeyframeGroup");

  assert.match(appSource, /continueActiveImageGenerationTask/);
  assert.match(appSource, /handleActiveImageTaskStillRunning/);
  assert.match(appSource, /activeImageTaskFollowUpRef/);
  assert.match(appSource, /generationTaskIsActive/);
  assert.match(generateSource, /handleActiveImageTaskStillRunning\(completedTask, ".*?"\)/s);
  assert.match(retryTaskSource, /handleActiveImageTaskStillRunning\(completedTask, `.*?\$\{slotIndex\}`\)/s);
  assert.match(retryGroupSource, /handleActiveImageTaskStillRunning\(completedTask, `.*?\$\{group\.group\}`\)/s);
  assert.doesNotMatch(
    generateSource,
    /if \(!generatedAssets\.length\) \{\s*setError\("鍥剧墖鐢熸垚澶辫触/s,
  );
});

test("keyframe groups expose whole-scheme retry separately from feedback regeneration", () => {
  assert.match(appSource, /handleRetryKeyframeGroupTask/);
  assert.match(appSource, /onRetryGroup/);
  assert.match(appSource, /重生方案/);
  assert.match(appSource, /按意见重生此方案/);
  assert.doesNotMatch(appSource, /brief 生成中/);
  assert.doesNotMatch(appSource, /图片下载保存中/);
});

test("performance analysis shows the current optimization work order and collapses secondary work", () => {
  const performanceSource = componentSource("PerformanceAnalysisView", "DataCompletenessCard");

  assert.match(performanceSource, /className="panel performance-current-panel"/);
  assert.match(performanceSource, /className="performance-current-card"/);
  assert.match(performanceSource, /className="performance-current-stats"/);
  assert.match(performanceSource, /className="performance-manual-details"/);
  assert.match(performanceSource, /className="performance-queue-details"/);
  assert.match(performanceSource, /const pendingAnalyses =/);
  assert.match(performanceSource, /const completedAnalyses =/);
  assert.match(performanceSource, /const exceptionAnalyses =/);
  assert.match(performanceSource, /performanceQueueStatus\(analysis\)/);
  assert.doesNotMatch(performanceSource, /className="panel performance-list-panel"/);

  const manualDetailsIndex = performanceSource.indexOf('className="performance-manual-details"');
  const manualFormIndex = performanceSource.indexOf('className="performance-manual-form"', manualDetailsIndex);
  assert.ok(manualDetailsIndex > 0, "manual create panel should be a collapsible details block");
  assert.ok(manualFormIndex > manualDetailsIndex, "manual form should live inside the collapsible details block");

  assert.match(appSource, /function performanceQueueStatus\(analysis: AdPerformanceAnalysis\):/);
  assert.match(stylesSource, /\.performance-current-panel\s*\{/);
  assert.match(stylesSource, /\.performance-current-card\s*\{/);
  assert.match(stylesSource, /\.performance-queue-details\s*\{/);
});

test("operator identity gates the workbench and selections claim records first", () => {
  assert.match(appSource, /function OperatorGate\(/);
  assert.match(appSource, /const \[currentOperatorId, setCurrentOperatorId\] = useState<string \| null>\(\(\) => getOperatorId\(\)\)/);
  assert.match(appSource, /if \(!currentOperator\) \{/);
  assert.match(appSource, /function OperatorBadge\(/);
  assert.match(appSource, /async function claimAndSelectJob\(jobId: string, targetView\?: ViewKey\)/);
  assert.match(appSource, /api\.claimAdGenerationJob\(jobId\)/);
  assert.match(appSource, /async function claimAndSelectPerformanceAnalysis\(analysisId: string\)/);
  assert.match(appSource, /api\.claimAdPerformanceAnalysis\(analysisId\)/);
  assert.doesNotMatch(appSource, /setSelectedJobId=\{setSelectedJobId\}/);
  assert.doesNotMatch(appSource, /setSelectedAnalysisId=\{setSelectedPerformanceAnalysisId\}/);
});

test("operator id and optimistic updated_at checks are sent through the api layer", () => {
  assert.match(apiSource, /const OPERATOR_ID_STORAGE_KEY = "ai_ads_operator_id"/);
  assert.match(apiSource, /"X-Operator-Id": operatorId/);
  assert.match(apiSource, /listOperators: \(\) => request<OperatorUser\[\]>\("\/operators"\)/);
  assert.match(apiSource, /claimAdGenerationJob: \(jobId: string\) =>/);
  assert.match(apiSource, /claimAdPerformanceAnalysis: \(analysisId: string\) =>/);
  assert.match(apiSource, /expected_updated_at: expectedUpdatedAt \?\? null/);
  assert.match(appSource, /api\.updateAdGenerationReview\(selectedJob\.id, parsed, "", selectedJob\.updated_at\)/);
  assert.match(appSource, /api\.confirmAdGenerationReview\(selectedJob\.id, parsed, "", selectedJob\.updated_at\)/);
});

test("task monitor view lists generation tasks and exposes retry", () => {
  assert.match(appSource, /\|\s*"tasks"/);
  assert.match(appSource, /\{\s*key:\s*"tasks"/);
  assert.match(appSource, /function TaskMonitorView\(/);
  assert.match(appSource, /className="task-monitor-layout"/);
  assert.match(appSource, /generationTaskMonitorStats/);
  assert.match(appSource, /generationTaskStatusDisplay\(task\)/);
  assert.match(appSource, /api\.listGenerationTasks/);
  assert.match(appSource, /api\.retryGenerationTask\(taskId\)/);
  assert.match(apiSource, /listGenerationTasks:/);
  assert.match(apiSource, /\/generation-tasks\?/);
  assert.match(stylesSource, /\.task-monitor-layout\s*\{/);
  assert.match(stylesSource, /\.task-table\s*\{/);
});

test("task monitor rows use readable work order context before technical ids", () => {
  const taskMonitorSource = componentSource("TaskMonitorView", "WorkflowProgress");

  assert.match(taskMonitorSource, /jobs:\s*AdGenerationJob\[\]/);
  assert.match(taskMonitorSource, /campaigns:\s*Campaign\[\]/);
  assert.match(taskMonitorSource, /topics:\s*Topic\[\]/);
  assert.match(taskMonitorSource, /drafts:\s*CopyDraft\[\]/);
  assert.match(taskMonitorSource, /videos:\s*VideoAsset\[\]/);
  assert.match(taskMonitorSource, /taskMonitorDisplayContext\(task,/);
  assert.match(taskMonitorSource, /className="task-work-order"/);
  assert.match(taskMonitorSource, /display\.workOrderLabel/);
  assert.match(taskMonitorSource, /display\.taskTypeLabel/);
  assert.match(taskMonitorSource, /display\.businessLabel/);
  assert.match(taskMonitorSource, /\u4efb\u52a1\u53f7 \{shortId\(task\.id\)\}/);
  assert.doesNotMatch(taskMonitorSource, /<span className="task-id">\{shortId\(task\.id\)\}<\/span>/);
  assert.match(appSource, /task\.display_context/);
  assert.doesNotMatch(appSource, /未关联工单/);
});

test("failed creative candidate retry is not blocked by unrelated loading state", () => {
  const creativeSlotSource = componentSource("CreativeSlotCard", "VideosView");
  const retryTaskSource = asyncFunctionSource("handleRetryCreativeSlotTask", "handleRetryCreativeSlot");

  assert.match(creativeSlotSource, /const canRetrySlot =/);
  assert.match(creativeSlotSource, /disabled=\{isRetrying\}/);
  assert.match(retryTaskSource, /if \(imageGenerationActionIsBusy\(loading\)\) \{\s*setNotice\(/s);
  assert.doesNotMatch(
    creativeSlotSource,
    /onClick=\{\(\) => onRetrySlot\(slot\.index\)\} disabled=\{Boolean\(loading\)\}/,
  );
});

test("task monitor auto refreshes active tasks silently", () => {
  assert.match(appSource, /const TASK_MONITOR_ACTIVE_REFRESH_MS = 3000;/);
  assert.match(appSource, /const TASK_MONITOR_IDLE_REFRESH_MS = 15000;/);
  assert.match(appSource, /const hasActiveGenerationTasks = useMemo\(/);
  assert.match(appSource, /generationTaskIsFinal\(task\)/);
  assert.match(appSource, /window\.setInterval/);
  assert.match(appSource, /refreshGenerationTasks\(\{ silent: true \}\)/);
  assert.match(
    appSource,
    /async function refreshGenerationTasks\(options: \{ silent\?: boolean \} = \{\}\): Promise<boolean>/,
  );
});

test("task monitor opens a detail drawer with failure advice and debug data", () => {
  const taskMonitorSource = componentSource("TaskMonitorView", "TaskDetailDrawer");
  const detailDrawerSource = componentSource("TaskDetailDrawer", "WorkflowProgress");

  assert.match(taskMonitorSource, /const \[selectedTaskId, setSelectedTaskId\]/);
  assert.match(taskMonitorSource, /setSelectedTaskId\(task\.id\)/);
  assert.match(taskMonitorSource, /<TaskDetailDrawer/);
  assert.match(detailDrawerSource, /generationTaskFailureAdvice\(task\)/);
  assert.match(detailDrawerSource, /className="task-detail-panel"/);
  assert.match(detailDrawerSource, /className="task-detail-advice"/);
  assert.match(detailDrawerSource, /className="task-detail-json"/);
  assert.match(stylesSource, /\.task-detail-panel\s*\{/);
  assert.match(stylesSource, /\.task-detail-json\s*\{/);
});

test("task monitor shows queue pressure analytics for concurrent usage", () => {
  const taskMonitorSource = componentSource("TaskMonitorView", "TaskDetailDrawer");

  assert.match(taskMonitorSource, /summary\.queue_health/);
  assert.match(taskMonitorSource, /summary\.target_concurrent_users/);
  assert.match(taskMonitorSource, /summary\.total_active_capacity/);
  assert.match(taskMonitorSource, /summary\.failure_codes/);
  assert.match(taskMonitorSource, /summary\.slowest_queues/);
  assert.match(taskMonitorSource, /className="task-analytics-grid"/);
  assert.match(taskMonitorSource, /className="task-queue-health-list"/);
  assert.match(taskMonitorSource, /className="task-failure-list"/);
  assert.match(taskMonitorSource, /generationTaskQueueRiskLabel/);
  assert.match(stylesSource, /\.task-analytics-grid\s*\{/);
  assert.match(stylesSource, /\.task-queue-health-card\s*\{/);
  assert.match(stylesSource, /\.task-risk-badge\.high\s*\{/);
});

test("task monitor shows redis backlog and celery worker health", () => {
  const taskMonitorSource = componentSource("TaskMonitorView", "TaskDetailDrawer");

  assert.match(taskMonitorSource, /summary\.execution_backend/);
  assert.match(taskMonitorSource, /summary\.redis_queues/);
  assert.match(taskMonitorSource, /summary\.worker_health/);
  assert.match(taskMonitorSource, /className="task-runtime-grid"/);
  assert.match(taskMonitorSource, /className="task-runtime-worker-list"/);
  assert.match(taskMonitorSource, /className="task-redis-depth"/);
  assert.match(taskMonitorSource, /workerHealth\.missing_queues/);
  assert.match(stylesSource, /\.task-runtime-grid\s*\{/);
  assert.match(stylesSource, /\.task-runtime-worker-list\s*\{/);
  assert.match(stylesSource, /\.task-redis-depth\s*\{/);
});
