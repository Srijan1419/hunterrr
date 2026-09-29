"use client";

import { Fragment } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface PaginationProps {
  currentPage: number;
  totalPages: number;
  totalCount: number;
  pageSize: number;
}

export function Pagination({ currentPage, totalPages, totalCount, pageSize }: PaginationProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const goToPage = (page: number) => {
    const params = new URLSearchParams(searchParams.toString());
    if (page > 1) {
      params.set("page", page.toString());
    } else {
      params.delete("page");
    }
    router.push(`/jobs?${params.toString()}`);
  };

  const startItem = (currentPage - 1) * pageSize + 1;
  const endItem = Math.min(currentPage * pageSize, totalCount);

  if (totalPages <= 1) {
    return (
      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>Showing {startItem}–{endItem} of {totalCount}</span>
      </div>
    );
  }

  const pages = Array.from({ length: totalPages }, (_, i) => i + 1);
  const visiblePages = pages.filter(
    (page) =>
      page === 1 ||
      page === totalPages ||
      (page >= currentPage - 1 && page <= currentPage + 1)
  );

  return (
    <nav className="flex flex-col sm:flex-row items-center justify-between gap-4 text-sm" aria-label="Pagination">
      <div className="text-muted-foreground">
        Showing {startItem}–{endItem} of {totalCount}
      </div>
      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          onClick={() => goToPage(currentPage - 1)}
          disabled={currentPage === 1}
          aria-label="Previous page"
        >
          ← Prev
        </Button>
        {visiblePages.map((page, index) => {
          const prevPage = visiblePages[index - 1];
          const showEllipsis = prevPage && page - prevPage > 1;
          return (
            <Fragment key={page}>
              {showEllipsis && (
                <span className="px-2 text-muted-foreground">…</span>
              )}
              <Button
                variant={page === currentPage ? "default" : "outline"}
                size="sm"
                onClick={() => goToPage(page)}
                aria-label={`Page ${page}`}
                aria-current={page === currentPage ? "page" : undefined}
              >
                {page}
              </Button>
            </Fragment>
          );
        })}
        <Button
          variant="outline"
          size="sm"
          onClick={() => goToPage(currentPage + 1)}
          disabled={currentPage === totalPages}
          aria-label="Next page"
        >
          Next →
        </Button>
      </div>
    </nav>
  );
}