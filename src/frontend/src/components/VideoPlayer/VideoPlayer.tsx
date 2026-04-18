import { useEffect, useRef } from "react";
import Hls from "hls.js";
import { useI18n } from "../../i18n";
import "./VideoPlayer.css";

interface VideoPlayerProps {
  src: string;
}

export default function VideoPlayer({ src }: VideoPlayerProps) {
  const { copy } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !src) return;

    if (Hls.isSupported()) {
      const hls = new Hls();
      hls.loadSource(src);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
      });
      return () => hls.destroy();
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      // Safari native HLS
      video.src = src;
      video.addEventListener("loadedmetadata", () => {
        video.play().catch(() => {});
      });
    }
  }, [src]);

  return (
    <div className="video-player card">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{copy.video.eyebrow}</span>
          <h3>{copy.video.title}</h3>
        </div>
      </div>
      <video ref={videoRef} controls className="video-element" />
    </div>
  );
}
