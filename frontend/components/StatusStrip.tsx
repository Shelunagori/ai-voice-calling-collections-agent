"use client";

// Live backend capabilities (GET /api/capabilities), shared by every page that shows backend status.

import { useEffect, useState } from "react";
import { Capabilities, getJSON } from "@/lib/api";

export function useCapabilities() {
  const [cap, setCap] = useState<Capabilities | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    getJSON<Capabilities>("/api/capabilities")
      .then(setCap)
      .catch((e: Error) => setError(e.message));
  }, []);
  return { cap, error };
}
