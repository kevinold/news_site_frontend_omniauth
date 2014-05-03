class Keyword
  include Mongoid::Document

  field :name
  #embedded_in :user
end
