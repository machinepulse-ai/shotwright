import { useEffect, useRef } from "react";
import Hls from "hls.js";
import { useI18n } from "../../i18n";
import "./VideoPlayer.css";

interface VideoPlayerProps {
  src: string;
  format: "mp4" | "hls";
  downloadUrl?: string | null;
  assetName?: string | null;
  projectName?: string | null;
}

export default function VideoPlayer({ src, format, downloadUrl, assetName, projectName }: VideoPlayerProps) {
  const { copy } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;

    const resetVideo = () => {
      video.pause();
      video.removeAttribute("src");
      video.load();
    };

    resetVideo();

    if (format === "mp4") {
      video.src = src;
      video.load();
      return () => resetVideo();
    }

    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
      });
      return () => {
        hls.destroy();
        resetVideo();
      };
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari native HLS
      video.src = src;
      video.addEventListener("loadedmetadata", () => {
        video.play().catch(() => {});
      });
      return () => resetVideo();
    }

    return () => resetVideo();
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
      <video ref={videoRef} controls preload="metadata" className="video-element" />
    </div>
  );
}
