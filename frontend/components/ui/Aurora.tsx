import ui from "./ui.module.css";

/**
 * Site-wide animated background: four blurred blobs plus film grain, fixed behind everything.
 * The fourth blob follows the live voice state through <html data-voice="…"> (set by the voice orb).
 * Pure CSS; animation stops under prefers-reduced-motion.
 */
export function Aurora() {
  return (
    <>
      <div className={ui.aurora} aria-hidden="true">
        <i />
        <i />
        <i />
        <i />
      </div>
      <div className={ui.grain} aria-hidden="true" />
    </>
  );
}
