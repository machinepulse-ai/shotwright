import { useEffect, useRef } from "react";
import Hls from "hls.js";
import { useI18n } from "../../i18n";
import { playMediaElement, resetMediaElement } from "../../utils/media";
import "./VideoPlayer.css";

interface VideoPlayerProps {
  src: string;
  format: "mp4" | "hls";
  downloadUrl?: string | null;
  assetName?: string | null;
  projectName?: string | null;
  poster?: string | null;
}

export default function VideoPlayer({ src, format, downloadUrl, assetName, projectName, poster }: VideoPlayerProps) {
  const { copy } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;

    resetMediaElement(video);

    if (format === "mp4") {
      video.src = src;
      video.load();
      return () => resetMediaElement(video);
    }

    if (Hls.isSupported()) {
      const hls = new Hls();
      const handleManifestParsed = () => playMediaElement(video);
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, handleManifestParsed);
      return () => {
        hls.off(Hls.Events.MANIFEST_PARSED, handleManifestParsed);
        hls.destroy();
        resetMediaElement(video);
      };
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari native HLS
      const handleLoadedMetadata = () => playMediaElement(video);
      video.src = src;
      video.addEventListener("loadedmetadata", handleLoadedMetadata);
      return () => {
        video.removeEventListener("loadedmetadata", handleLoadedMetadata);
        resetMediaElement(video);
      };
    }

    return () => resetMediaElement(video);
  }, [format, src]);

  return (
    <div className="video-player card">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{copy.video.eyebrow}</span>
          <h3>{copy.video.title}</h3>
          {assetName || projectName ? (
            <div className="video-player-meta">
              {assetName ? <span>{`${copy.video.latestLabel}: ${assetName}`}</span> : null}
              {projectName ? <span>{`${copy.video.projectLabel}: ${projectName}`}</span> : null}
            </div>
          ) : null}
        </div>
        <div className="video-player-actions">
          <span className={`video-source-badge format-${format}`}>{format === "mp4" ? copy.video.sourceMp4 : copy.video.sourceHls}</span>
          <a className="ghost-button btn-sm" href={downloadUrl || src} target="_blank" rel="noreferrer">
            {copy.video.open}
          </a>
        </div>
      </div>
      <video ref={videoRef} controls preload="metadata" poster={poster || undefined} className="video-element" />
    </div>
  );
}
