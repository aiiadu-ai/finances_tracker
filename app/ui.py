"""Which blocks of the screens are hidden right now.

A hidden block is only left out of the page: its numbers are still counted, its markup stays in the
template, and its address still works. To bring a block back, delete its name from HIDDEN_BLOCKS.

  balance   Home: the Balance card (Income − Expenses of the month)
  people    Home: "Sent to people" / "Received from people" (Free still counts them)
  recent    Home: the Recent entries
  change    Analytics: "Expenses / Income this month so far" against the previous month
  trend     Analytics: "Expenses by month" and "Income by month"
  forecast  Analytics: the forecast to the end of the month
"""

HIDDEN_BLOCKS = frozenset({"balance", "people", "recent", "change", "trend", "forecast"})
