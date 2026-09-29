"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { removeSavedSearch } from "@/lib/mutations/user-actions";

interface RemoveSavedSearchButtonProps {
  savedSearchId: string;
}

/**
 * Remove one saved search from the signed-in user's dashboard.
 *
 * The action it calls scopes the delete by the session's user id, not by the id
 * prop - the id only ever selects WHICH of the caller's own rows goes, never WHOSE
 * rows are eligible. router.refresh() re-renders the server component with the new
 * list; the button hides itself immediately so the row doesn't linger while the
 * refresh is in flight.
 */
export function RemoveSavedSearchButton({ savedSearchId }: RemoveSavedSearchButtonProps) {
  const router = useRouter();
  const [isRemoving, setIsRemoving] = useState(false);
  const [isRemoved, setIsRemoved] = useState(false);

  const handleClick = async () => {
    if (isRemoving) return;
    setIsRemoving(true);

    try {
      const removed = await removeSavedSearch(savedSearchId);
      if (removed) {
        setIsRemoved(true);
        router.refresh();
      }
    } catch (error) {
      console.error("Remove saved search failed:", error);
      alert("Failed to remove the saved search. Please try again.");
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
