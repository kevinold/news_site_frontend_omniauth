class HomeController < ApplicationController
  def index
    @stories = Harvest.all
    @keywords = Keyword.all
  end
end
