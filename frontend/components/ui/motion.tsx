"use client";

import { LazyMotion, MotionConfig } from "framer-motion";

// Animation features (incl. layout animations) load in their own chunk after first paint; until then `m.*`
// elements render statically. reducedMotion="user" honours prefers-reduced-motion everywhere below.
const features = () => import("framer-motion").then((mod) => mod.domMax);

export function MotionRoot({ children }: { children: React.ReactNode }) {
  return (
    <LazyMotion features={features} strict>
      <MotionConfig reducedMotion="user">{children}</MotionConfig>
    </LazyMotion>
  );
}

export const SPRING = { type: "spring", stiffness: 140, damping: 22, mass: 0.9 } as const;
