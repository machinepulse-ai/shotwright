import Hls, { ErrorDetails, ErrorTypes, Events, type ErrorData } from "hls.js";

type VideoFormat = "mp4" | "hls";

type VideoSourceBindingOptions = {
  autoplay?: boolean;
};

export function isExpectedMediaAbortError(error: unknown) {
  const name = typeof (error as { name?: unknown })?.name === "string" ? String((error as { name: string }).name) : "";
  const message =
    typeof (error as { message?: unknown })?.message === "string" ? String((error as { message: string }).message) : "";

  return (
    name === "AbortError" ||
    /AbortError|operation was aborted|play\(\) request was interrupted|media operation was aborted|loading was aborted/i.test(message)
  );
}

function errorMessage(error: unknown) {
  if (!error) return "";
  if (typeof error === "string") return error;
  if (typeof (error as { message?: unknown }).message === "string") {
    return String((error as { message: string }).message);
  }
  return String(error);
}

function isExpectedHlsAbortError(data: ErrorData) {
  const networkDetails = data.networkDetails as { name?: string; message?: string; error?: unknown } | undefined;
  return (
    data.details === ErrorDetails.INTERNAL_ABORTED ||
    isExpectedMediaAbortError(data.error) ||
    isExpectedMediaAbortError(data.err) ||
    isExpectedMediaAbortError(networkDetails) ||
    isExpectedMediaAbortError(networkDetails?.error) ||
    /abort|aborted|operation was aborted/i.test([data.reason, data.url, errorMessage(data.error)].filter(Boolean).join(" "))
  );
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

export function bindVideoSource(
  video: HTMLVideoElement,
  src: string,
  format: VideoFormat,
  options: VideoSourceBindingOptions = {},
) {
  let disposed = false;

  resetMediaElement(video);

  const cleanupVideoOnly = () => {
    disposed = true;
    resetMediaElement(video);
  };

  if (format === "mp4") {
    video.src = src;
    video.load();
    if (options.autoplay) {
      playMediaElement(video);
    }
    return cleanupVideoOnly;
  }

  if (Hls.isSupported()) {
    const hls = new Hls({
      backBufferLength: 90,
      maxBufferLength: 30,
    });
    let networkRecoveries = 0;
    let mediaRecoveries = 0;

    const handleManifestParsed = () => {
      if (!disposed && options.autoplay) {
        playMediaElement(video);
      }
    };

    const handleError = (_event: Events.ERROR, data: ErrorData) => {
      if (disposed || isExpectedHlsAbortError(data)) {
        return;
      }

      if (!data.fatal) {
        console.debug("HLS playback warning:", data.details, data.error || data.reason || "");
        return;
      }

      if (data.type === ErrorTypes.NETWORK_ERROR && networkRecoveries < 2) {
        networkRecoveries += 1;
        try {
          hls.startLoad();
        } catch (error) {
          console.debug("HLS network recovery skipped:", error);
        }
        return;
      }

      if (data.type === ErrorTypes.MEDIA_ERROR && mediaRecoveries < 2) {
        mediaRecoveries += 1;
        try {
          hls.recoverMediaError();
        } catch (error) {
          console.debug("HLS media recovery skipped:", error);
        }
        return;
      }

      console.debug("HLS playback stopped:", data.details, data.error || data.reason || "");
    };

    hls.on(Events.MANIFEST_PARSED, handleManifestParsed);
    hls.on(Events.ERROR, handleError);
    hls.attachMedia(video);
    hls.loadSource(src);

    return () => {
      disposed = true;
      hls.off(Events.MANIFEST_PARSED, handleManifestParsed);
      hls.off(Events.ERROR, handleError);
      try {
        hls.stopLoad();
      } catch (error) {
        if (!isExpectedMediaAbortError(error)) {
          console.debug("HLS stop skipped:", error);
        }
      }
      try {
        hls.detachMedia();
      } catch (error) {
        if (!isExpectedMediaAbortError(error)) {
          console.debug("HLS detach skipped:", error);
        }
      }
      try {
        hls.destroy();
      } catch (error) {
        if (!isExpectedMediaAbortError(error)) {
          console.debug("HLS destroy skipped:", error);
        }
      }
      resetMediaElement(video);
    };
  }

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    const handleLoadedMetadata = () => {
      if (!disposed && options.autoplay) {
        playMediaElement(video);
      }
    };
    const handleNativeError = () => {
      if (!disposed && video.error) {
        console.debug("Native HLS playback warning:", video.error.message || video.error.code);
      }
    };
    video.addEventListener("loadedmetadata", handleLoadedMetadata);
    video.addEventListener("error", handleNativeError);
    video.src = src;
    video.load();

    return () => {
      disposed = true;
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
      video.removeEventListener("error", handleNativeError);
      resetMediaElement(video);
    };
  }

  return cleanupVideoOnly;
}
