import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TranslationCopy } from "../../i18n";
import "./FirstRunGuide.css";

const STORAGE_KEY = "shotwright_first_run_guide_v1";
const TARGET_PADDING = 8;
const VIEWPORT_PADDING = 12;
const POPOVER_GAP = 12;

type GuideStepKey = keyof TranslationCopy["app"]["firstRunGuide"]["steps"];
type Placement = "top" | "right" | "bottom" | "left";

type GuideStep = {
  key: GuideStepKey;
  selector: string;
  fallbackSelector?: string;
  placement: Placement;
  title: string;
  body: string;
};

type GuideRect = {
  top: number;
  right: number;
  bottom: number;
  left: number;
  width: number;
  height: number;
};

type PopoverPosition = {
  top: number;
  left: number;
  placement: Placement;
};

type FirstRunGuideProps = {
  copy: TranslationCopy["app"]["firstRunGuide"];
  enabled: boolean;
  onStepChange?: (stepKey: GuideStepKey) => void;
};

function hasCompletedGuide() {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function persistGuideComplete() {
  try {
    window.localStorage.setItem(STORAGE_KEY, "1");
  } catch {
    // Ignore storage failures; the in-memory dismissal still applies.
  }
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function nearlyEqual(first: number, second: number) {
  return Math.abs(first - second) < 0.5;
}

function isSameRect(first: GuideRect | null, second: GuideRect | null) {
  if (!first && !second) return true;
  if (!first || !second) return false;

  return (
    nearlyEqual(first.top, second.top) &&
    nearlyEqual(first.right, second.right) &&
    nearlyEqual(first.bottom, second.bottom) &&
    nearlyEqual(first.left, second.left) &&
    nearlyEqual(first.width, second.width) &&
    nearlyEqual(first.height, second.height)
  );
}

function isSamePosition(first: PopoverPosition, second: PopoverPosition) {
  return first.placement === second.placement && nearlyEqual(first.top, second.top) && nearlyEqual(first.left, second.left);
}

function getViewportRect() {
  const visualViewport = window.visualViewport;
  return {
    width: Math.round(visualViewport?.width ?? window.innerWidth),
    height: Math.round(visualViewport?.height ?? window.innerHeight),
  };
}

function isUsableTarget(element: HTMLElement | null): element is HTMLElement {
  if (!element || element.hidden) return false;

  const rect = element.getBoundingClientRect();
  if (rect.width < 1 || rect.height < 1) return false;

  const styles = window.getComputedStyle(element);
  return styles.display !== "none" && styles.visibility !== "hidden" && Number(styles.opacity) !== 0;
}

function resolveTarget(selector: string, fallbackSelector?: string) {
  const primary = document.querySelector<HTMLElement>(selector);
  if (isUsableTarget(primary)) return primary;

  if (fallbackSelector) {
    const fallback = document.querySelector<HTMLElement>(fallbackSelector);
    if (isUsableTarget(fallback)) return fallback;
  }

  return null;
}

function getGuideRect(element: HTMLElement): GuideRect {
  const rect = element.getBoundingClientRect();
  return {
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
    left: rect.left,
    width: rect.width,
    height: rect.height,
  };
}

function getExpandedRect(rect: GuideRect, viewportWidth: number, viewportHeight: number): GuideRect {
  const top = clamp(rect.top - TARGET_PADDING, 0, viewportHeight);
  const left = clamp(rect.left - TARGET_PADDING, 0, viewportWidth);
  const right = clamp(rect.right + TARGET_PADDING, 0, viewportWidth);
  const bottom = clamp(rect.bottom + TARGET_PADDING, 0, viewportHeight);

  return {
    top,
    right,
    bottom,
    left,
    width: Math.max(0, right - left),
    height: Math.max(0, bottom - top),
  };
}

function choosePlacement(rect: GuideRect, popoverWidth: number, popoverHeight: number, preferred: Placement) {
  const viewport = getViewportRect();
  const spaces: Record<Placement, number> = {
    top: rect.top,
    right: viewport.width - rect.right,
    bottom: viewport.height - rect.bottom,
    left: rect.left,
  };
  const requiredSpace: Record<Placement, number> = {
    top: popoverHeight + POPOVER_GAP,
    right: popoverWidth + POPOVER_GAP,
    bottom: popoverHeight + POPOVER_GAP,
    left: popoverWidth + POPOVER_GAP,
  };

  if (spaces[preferred] >= requiredSpace[preferred]) {
    return preferred;
  }

  return (Object.keys(spaces) as Placement[]).sort((a, b) => spaces[b] - spaces[a])[0];
}

function getPopoverPosition(rect: GuideRect | null, popover: HTMLDivElement | null, preferred: Placement): PopoverPosition {
  const viewport = getViewportRect();
  const width = popover?.offsetWidth || Math.min(340, viewport.width - VIEWPORT_PADDING * 2);
  const height = popover?.offsetHeight || 210;
  const maxLeft = Math.max(VIEWPORT_PADDING, viewport.width - width - VIEWPORT_PADDING);
  const maxTop = Math.max(VIEWPORT_PADDING, viewport.height - height - VIEWPORT_PADDING);

  if (!rect) {
    return {
      top: clamp((viewport.height - height) / 2, VIEWPORT_PADDING, maxTop),
      left: clamp((viewport.width - width) / 2, VIEWPORT_PADDING, maxLeft),
      placement: "bottom",
    };
  }

  const placement = choosePlacement(rect, width, height, preferred);
  let top = rect.bottom + POPOVER_GAP;
  let left = rect.left + rect.width / 2 - width / 2;

  if (placement === "top") {
    top = rect.top - height - POPOVER_GAP;
    left = rect.left + rect.width / 2 - width / 2;
  }

  if (placement === "right") {
    top = rect.top + rect.height / 2 - height / 2;
    left = rect.right + POPOVER_GAP;
  }

  if (placement === "left") {
    top = rect.top + rect.height / 2 - height / 2;
    left = rect.left - width - POPOVER_GAP;
  }

  return {
    top: clamp(top, VIEWPORT_PADDING, maxTop),
    left: clamp(left, VIEWPORT_PADDING, maxLeft),
    placement,
  };
}

export default function FirstRunGuide({ copy, enabled, onStepChange }: FirstRunGuideProps) {
  const [isVisible, setIsVisible] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<GuideRect | null>(null);
  const [popoverPosition, setPopoverPosition] = useState<PopoverPosition>({ top: VIEWPORT_PADDING, left: VIEWPORT_PADDING, placement: "bottom" });
  const popoverRef = useRef<HTMLDivElement | null>(null);

  const steps = useMemo<GuideStep[]>(
    () => [
      {
        key: "sessions",
        selector: '[data-testid="toggle-session-sidebar"]',
        placement: "bottom",
        ...copy.steps.sessions,
      },
      {
        key: "newChat",
        selector: '[data-testid="sidebar-new-chat"]',
        fallbackSelector: '[data-testid="toggle-session-sidebar"]',
        placement: "right",
        ...copy.steps.newChat,
      },
      {
        key: "composer",
        selector: '[data-testid="composer-prompt-input"]',
        placement: "top",
        ...copy.steps.composer,
      },
      {
        key: "attachments",
        selector: '[data-testid="composer-attachment-trigger"]',
        placement: "top",
        ...copy.steps.attachments,
      },
      {
        key: "agentSettings",
        selector: '[data-testid="session-settings-card"]',
        fallbackSelector: '[data-testid="session-model-select"]',
        placement: "top",
        ...copy.steps.agentSettings,
      },
      {
        key: "details",
        selector: '[data-testid="toggle-context-sidebar"]',
        placement: "bottom",
        ...copy.steps.details,
      },
      {
        key: "theme",
        selector: '[data-testid="toggle-color-theme"]',
        placement: "bottom",
        ...copy.steps.theme,
      },
    ],
    [copy],
  );

  const activeStep = steps[activeIndex] ?? steps[0];
  const isLastStep = activeIndex >= steps.length - 1;

  const updatePosition = useCallback(() => {
    if (!activeStep) return;

    const viewport = getViewportRect();
    const target = resolveTarget(activeStep.selector, activeStep.fallbackSelector);
    const nextTargetRect = target ? getExpandedRect(getGuideRect(target), viewport.width, viewport.height) : null;

    setTargetRect((currentRect) => (isSameRect(currentRect, nextTargetRect) ? currentRect : nextTargetRect));
    setPopoverPosition((currentPosition) => {
      const nextPosition = getPopoverPosition(nextTargetRect, popoverRef.current, activeStep.placement);
      return isSamePosition(currentPosition, nextPosition) ? currentPosition : nextPosition;
    });
  }, [activeStep]);

  useEffect(() => {
    if (!enabled || hasCompletedGuide()) {
      setIsVisible(false);
      return;
    }

    setIsVisible(true);
  }, [enabled]);

  useEffect(() => {
    if (!isVisible || !activeStep) return;

    onStepChange?.(activeStep.key);

    let frameId = window.requestAnimationFrame(updatePosition);
    const delayedPositionTimer = window.setTimeout(updatePosition, 180);
    const observer = new MutationObserver(updatePosition);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "hidden"] });

    const handleViewportChange = () => {
      window.cancelAnimationFrame(frameId);
      frameId = window.requestAnimationFrame(updatePosition);
    };

    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    window.visualViewport?.addEventListener("resize", handleViewportChange);
    window.visualViewport?.addEventListener("scroll", handleViewportChange);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.clearTimeout(delayedPositionTimer);
      observer.disconnect();
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
      window.visualViewport?.removeEventListener("resize", handleViewportChange);
      window.visualViewport?.removeEventListener("scroll", handleViewportChange);
    };
  }, [activeStep, isVisible, onStepChange, updatePosition]);

  const completeGuide = () => {
    persistGuideComplete();
    setIsVisible(false);
  };

  const goToPreviousStep = () => {
    setActiveIndex((current) => Math.max(0, current - 1));
  };

  const goToNextStep = () => {
    if (isLastStep) {
      completeGuide();
      return;
    }

    setActiveIndex((current) => Math.min(steps.length - 1, current + 1));
  };

  if (!enabled || !isVisible || !activeStep) {
    return null;
  }

  return (
    <div className="first-run-guide" data-testid="first-run-guide" aria-label={copy.landmarkLabel}>
      <div className="first-run-guide-scrim top" style={{ height: targetRect?.top ?? 0 }} />
      <div className="first-run-guide-scrim right" style={{ top: targetRect?.top ?? 0, left: targetRect?.right ?? 0, bottom: targetRect ? `calc(100% - ${targetRect.bottom}px)` : 0 }} />
      <div className="first-run-guide-scrim bottom" style={{ top: targetRect?.bottom ?? 0 }} />
      <div className="first-run-guide-scrim left" style={{ top: targetRect?.top ?? 0, width: targetRect?.left ?? 0, bottom: targetRect ? `calc(100% - ${targetRect.bottom}px)` : 0 }} />

      {targetRect ? (
        <div
          className="first-run-guide-spotlight"
          aria-hidden="true"
          style={{
            top: targetRect.top,
            left: targetRect.left,
            width: targetRect.width,
            height: targetRect.height,
          }}
        />
      ) : null}

      <div
        ref={popoverRef}
        className="first-run-guide-popover"
        data-placement={popoverPosition.placement}
        role="dialog"
        aria-modal="false"
        aria-labelledby="first-run-guide-title"
        style={{ top: popoverPosition.top, left: popoverPosition.left }}
      >
        <span className="first-run-guide-progress">{copy.progress.replace("{current}", String(activeIndex + 1)).replace("{total}", String(steps.length))}</span>
        <h2 id="first-run-guide-title">{activeStep.title}</h2>
        <p>{activeStep.body}</p>
        <div className="first-run-guide-actions">
          <button type="button" className="first-run-guide-link-button" onClick={completeGuide}>
            {copy.skip}
          </button>
          <div className="first-run-guide-step-actions">
            <button type="button" className="first-run-guide-secondary-button" onClick={goToPreviousStep} disabled={activeIndex === 0}>
              {copy.previous}
            </button>
            <button type="button" className="first-run-guide-primary-button" onClick={goToNextStep}>
              {isLastStep ? copy.finish : copy.next}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
