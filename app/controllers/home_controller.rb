class HomeController < ApplicationController
  def index
    @stories = Harvest.all
  end
end
