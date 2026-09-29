"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage } from "@/components/ui/form";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { saveSearch } from "@/lib/mutations/user-actions";

const saveSearchSchema = z.object({
  name: z.string().min(1, "Name is required").max(100, "Name too long"),
});

type SaveSearchFormData = z.infer<typeof saveSearchSchema>;

interface SaveSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  filters: Record<string, string | undefined>;
  onSuccess?: () => void;
}

export function SaveSearchModal({ isOpen, onClose, filters, onSuccess }: SaveSearchModalProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const {
    control,
    handleSubmit,
    formState: { errors },
    reset,
  } = useForm<SaveSearchFormData>({
    resolver: zodResolver(saveSearchSchema),
    defaultValues: { name: "" },
  });

  const onSubmit = async (data: SaveSearchFormData) => {
    setIsSubmitting(true);
    setError(null);

    try {
      const result = await saveSearch(data.name, filters);
      if (result) {
        reset();
        onClose();
        onSuccess?.();
      } else {
        setError("You must be signed in to save searches.");
      }
    } catch (err) {
      setError("Failed to save search. Please try again.");
      console.error("Save search failed:", err);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50" onClick={onClose}>
      <div
        className="w-full max-w-md bg-card rounded-lg shadow-lg p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-xl font-semibold mb-4">Save Search</h2>
        <p className="text-sm text-muted-foreground mb-4">
          Give this search a name so you can find it later on your dashboard.
        </p>

        <Form onSubmit={handleSubmit(onSubmit)}>
          <FormField
            control={control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Search Name</FormLabel>
                <FormControl>
                  <Input
                    placeholder="e.g. Senior Python jobs in US"
                    {...field}
                  />
                </FormControl>
                <FormMessage>
                  {errors.name?.message || error}
                </FormMessage>
              </FormItem>
            )}
          />

          <div className="flex gap-2 mt-6">
            <Button type="button" variant="outline" onClick={onClose} disabled={isSubmitting} className="flex-1">
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting} className="flex-1">
              {isSubmitting ? "Saving..." : "Save Search"}
            </Button>
          </div>
        </Form>
      </div>
    </div>
  );
}