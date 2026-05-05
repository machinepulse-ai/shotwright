export function isExpectedMediaAbortError(error: unknown) {
  const name = typeof (error as { name?: unknown })?.name === "string" ? String((error as { name: string }).name) : "";
  const message =
    typeof (error as { message?: unknown })?.message === "string" ? String((error as { message: string }).message) : "";

  return name === "AbortError" && /operation was aborted|play\(\) request was interrupted|media operation was aborted/i.test(message);
}

export function resetMediaElement(video: HTMLMediaElement) {
  try {
    video.pause();
  } catch {
    // Ignore browser media state races while React is unmounting or switching sources.
  }

  try {
    video.removeAttribute("src");
    video.load();
  } catch (error) {
    if (!isExpectedMediaAbortError(error)) {
      console.debug("Media reset skipped:", error);
    }
  }
}

export function playMediaElement(video: HTMLMediaElement) {
  try {
    const playPromise = video.play();
    if (playPromise) {
      void playPromise.catch((error) => {
        const name = typeof (error as { name?: unknown })?.name === "string" ? String((error as { name: string }).name) : "";
        if (name !== "NotAllowedError" && !isExpectedMediaAbortError(error)) {
          console.debug("Media autoplay skipped:", error);
        }
      });
    }
  } catch (error) {
    if (!isExpectedMediaAbortError(error)) {
      console.debug("Media playback skipped:", error);
    }
  }
}
