module ApplicationHelper
end

def format_newsfeed_date(dirty_date_string)

  dirty_date = Time.strptime(dirty_date_string, "%a %b %d %H:%M:%S %z %Y")
  time_now = Time.zone.now
  difference = (time_now - dirty_date.utc).round / 60

  #if within last minute
  if dirty_date.utc > (time_now - 60)

    "1 minute"

  #if within last hour
  elsif dirty_date.utc > (time_now - 60*60)

    "#{difference} minutes"

  #if within last day
  elsif dirty_date.utc > (time_now - 24*60*60)

    hours = difference / 60
    "#{hours} hours"

  else

    dirty_date.strftime("%b %e, %Y")

  end
end