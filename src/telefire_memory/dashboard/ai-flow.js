"use strict";

const stepNames = ["事件", "授权", "回复链", "记忆", "Prompt", "API", "回复", "写回"];
const stepTabs = Array.from(document.querySelectorAll(".step-tab"));
const stepPanels = Array.from(document.querySelectorAll(".step-panel"));
const threadMessages = new Map(
  Array.from(document.querySelectorAll("[data-message]")).map((element) => [
    element.dataset.message,
    element,
  ]),
);
const previousButton = document.querySelector("#previous-step");
const nextButton = document.querySelector("#next-step");
const stepStatus = document.querySelector("#step-status");
const progressBar = document.querySelector("#trace-progress-bar");
const playButton = document.querySelector("#play-flow");
const playLabel = document.querySelector("#play-label");
const answerPreview = document.querySelector("#answer-preview");
const viewTabs = Array.from(document.querySelectorAll("[data-view]"));
const requestViews = new Map(
  Array.from(document.querySelectorAll(".request-view")).map((element) => [
    element.id.replace("view-", ""),
    element,
  ]),
);
const copyStatus = document.querySelector("#copy-status");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

let activeStep = 0;
let playbackTimer = null;

function answerTextForStep(step) {
  if (step < 5) return "尚未创建回复";
  if (step === 5) return "Thinking…";
  return "今天先确认支付回归负责人和通过标准，再演练 migration 回滚；发布前记录 demo 版本与验证结果。";
}

function highlightSources(panel) {
  for (const message of threadMessages.values()) {
    message.classList.remove("is-active-source");
  }
  const sources = (panel.dataset.sources || "").split(" ").filter(Boolean);
  for (const source of sources) {
    threadMessages.get(source)?.classList.add("is-active-source");
  }
}

function setStep(nextStep, { focusTab = false } = {}) {
  activeStep = Math.max(0, Math.min(stepTabs.length - 1, nextStep));
  document.body.dataset.step = String(activeStep);

  stepTabs.forEach((tab, index) => {
    const selected = index === activeStep;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focusTab) tab.focus();
  });

  stepPanels.forEach((panel, index) => {
    panel.hidden = index !== activeStep;
  });

  const activePanel = stepPanels[activeStep];
  highlightSources(activePanel);
  answerPreview.textContent = answerTextForStep(activeStep);
  progressBar.style.width = `${((activeStep + 1) / stepTabs.length) * 100}%`;
  stepStatus.textContent = `第 ${activeStep + 1} 步，共 ${stepTabs.length} 步：${stepNames[activeStep]}`;
  previousButton.disabled = activeStep === 0;
  nextButton.disabled = activeStep === stepTabs.length - 1;
}

function stopPlayback() {
  if (playbackTimer !== null) {
    window.clearInterval(playbackTimer);
    playbackTimer = null;
  }
  playButton.setAttribute("aria-pressed", "false");
  playLabel.textContent = "自动播放";
}

function startPlayback() {
  if (reducedMotion.matches) {
    setStep(stepTabs.length - 1);
    stepStatus.textContent = "已遵循减少动态效果设置，直接显示最后一步：写回";
    return;
  }
  if (activeStep === stepTabs.length - 1) setStep(0);
  playButton.setAttribute("aria-pressed", "true");
  playLabel.textContent = "暂停";
  playbackTimer = window.setInterval(() => {
    if (activeStep >= stepTabs.length - 1) {
      stopPlayback();
      return;
    }
    setStep(activeStep + 1);
  }, 1150);
}

function togglePlayback() {
  if (playbackTimer !== null) stopPlayback();
  else startPlayback();
}

function setRequestView(viewName, { focusTab = false } = {}) {
  viewTabs.forEach((tab) => {
    const selected = tab.dataset.view === viewName;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focusTab) tab.focus();
  });
  for (const [name, view] of requestViews) {
    view.hidden = name !== viewName;
  }
}

function moveTab(event, tabs, currentIndex, activate) {
  let nextIndex = currentIndex;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") nextIndex += 1;
  else if (event.key === "ArrowLeft" || event.key === "ArrowUp") nextIndex -= 1;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = tabs.length - 1;
  else return;

  event.preventDefault();
  const normalized = (nextIndex + tabs.length) % tabs.length;
  activate(normalized);
}

async function copyTarget(button) {
  const target = document.querySelector(`#${button.dataset.copyTarget}`);
  if (!target) return;
  try {
    await navigator.clipboard.writeText(target.textContent.trim());
    copyStatus.textContent = "JSON 已复制到剪贴板。";
    button.textContent = "已复制";
    window.setTimeout(() => {
      button.textContent = "复制 JSON";
      copyStatus.textContent = "";
    }, 1800);
  } catch (_error) {
    copyStatus.textContent = "浏览器未允许复制，请手动选择代码。";
  }
}

stepTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => {
    stopPlayback();
    setStep(index);
  });
  tab.addEventListener("keydown", (event) => {
    moveTab(event, stepTabs, index, (nextIndex) => {
      stopPlayback();
      setStep(nextIndex, { focusTab: true });
    });
  });
});

previousButton.addEventListener("click", () => {
  stopPlayback();
  setStep(activeStep - 1);
});

nextButton.addEventListener("click", () => {
  stopPlayback();
  setStep(activeStep + 1);
});

playButton.addEventListener("click", togglePlayback);

viewTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => setRequestView(tab.dataset.view));
  tab.addEventListener("keydown", (event) => {
    moveTab(event, viewTabs, index, (nextIndex) => {
      setRequestView(viewTabs[nextIndex].dataset.view, { focusTab: true });
    });
  });
});

document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", () => copyTarget(button));
});

window.addEventListener("pagehide", stopPlayback);

setStep(0);
setRequestView("messages");
