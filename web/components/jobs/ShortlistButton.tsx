"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { addToShortlist, isShortlisted, removeFromShortlist } from "@/lib/mutations/user-actions";

interface ShortlistButtonProps {
  jobId: string;
  initialShortlisted?: boolean;
}

export function ShortlistButton({ jobId, initialShortlisted = false }: ShortlistButtonProps) {
  const [isShortlistedState, setIsShortlistedState] = useState(initialShortlisted);
  const [isLoading, setIsLoading] = useState(false);

  const handleClick = async () => {
    if (isLoading) return;
    setIsLoading(true);

    try {
      if (isShortlistedState) {
        const removed = await removeFromShortlist(jobId);
        if (removed) {
          setIsShortlistedState(false);
        }
      } else {
        const result = await addToShortlist(jobId);
        if (result) {
          setIsShortlistedState(true);
        }
      }
    } catch (error) {
      console.error("Shortlist action failed:", error);
      alert("Failed to update shortlist. Please sign in and try again.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Button
      variant={isShortlistedState ? "default" : "outline"}
      size="sm"
      onClick={handleClick}
      disabled={isLoading}
      className="whitespace-nowrap"
    >
      {isLoading ? (
        "Saving..."
      ) : isShortlistedState ? (
        "★ Shortlisted"
      ) : (
        "☆ Shortlist"
      )}
    </Button>
  );
}