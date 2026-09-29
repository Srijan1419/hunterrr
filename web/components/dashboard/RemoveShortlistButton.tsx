"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { removeFromShortlist } from "@/lib/mutations/user-actions";

interface RemoveShortlistButtonProps {
  jobId: string;
}

/**
 * Remove one shortlisted job from the signed-in user's dashboard.
 *
 * Reuses the /jobs ShortlistButton's action rather than adding a dashboard-only
 * one: removeFromShortlist deletes WHERE user_id = session AND job_id = ?, so
 * clicking here cannot touch another user's shortlist even with a guessed job id.
 * router.refresh() re-renders the server component with the new list.
 */
export function RemoveShortlistButton({ jobId }: RemoveShortlistButtonProps) {
  const router = useRouter();
  const [isRemoving, setIsRemoving] = useState(false);
  const [isRemoved, setIsRemoved] = useState(false);

  const handleClick = async () => {
    if (isRemoving) return;
    setIsRemoving(true);

    try {
      const removed = await removeFromShortlist(jobId);
      if (removed) {
        setIsRemoved(true);
        router.refresh();
      }
    } catch (error) {
      console.error("Remove shortlisted job failed:", error);
      alert("Failed to remove the shortlisted job. Please try again.");
    } finally {
      setIsRemoving(false);
    }
  };

  if (isRemoved) return null;

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={handleClick}
      disabled={isRemoving}
      className="whitespace-nowrap text-muted-foreground"
    >
      {isRemoving ? "Removing..." : "Remove"}
    </Button>
  );
}
